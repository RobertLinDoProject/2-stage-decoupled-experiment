# 高階 GPU 電腦搬移與執行指南

本指南用於將 Decoupled 2-Stage Experiment 搬到另一台具備較強 GPU 的電腦。搬移目標是：保留正式 `Data/`，以新電腦建立新的 Run，不攜帶舊的 Run 結果或 `.env`。portable package 會提供空白的 `storage` 寫入骨架，讓新電腦的結果直接保存到主機端。

## 1. 加速範圍

高階 GPU 主要加速本機 Ollama 的 M6 GAI action generation。M0～M5、M7～M9 仍主要使用 API container 的 CPU、記憶體與磁碟。

目前執行器以單一背景 worker 逐筆呼叫 Ollama，先維持單工以避免 VRAM 暴增與 request 交錯。搬移後若單次 latency 下降，整體 Run 會縮短，但不會按照 GPU 理論 FLOPS 等比例縮短。

## 2. 建議硬體與軟體

- NVIDIA driver 與可用的 NVIDIA GPU。
- Docker Desktop、WSL2 與 Docker Compose。
- 主機原生 Ollama；API／Frontend 仍由 Docker 執行。
- 至少能容納 `mistral:7b-instruct-v0.3-q4_K_M` 的 VRAM。若模型無法完整載入 GPU，可能發生 CPU offload，速度會明顯下降。

## 3. 建立 portable package

在原電腦專案根目錄執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package_portable_runtime.ps1
```

輸出：

```text
portable_packages/decoupled_2_stage_runtime_<timestamp>.zip
portable_packages/decoupled_2_stage_runtime_<timestamp>/portable_manifest.json
```

Package 包含 `backend`、`frontend`、`contracts`、`configs`、`Data`、`scripts`、Docker 設定、研究文件，以及空白可寫入的 `storage/tmp`、`storage/published/runs` 骨架。Package 不包含 `.env`、舊 Run、舊 artifacts、`node_modules`、`dist` 或 cache。

`portable_manifest.json` 會保存每個檔案與正式 `Data/` 的 SHA-256 checksum。

## 4. 新電腦安裝與啟動

先解壓 package，再建立主機端 Python 虛擬環境。主機端所有 Python 輔助腳本與測試都必須使用這個 `.venv`；API／Frontend 則由 Docker container 隔離執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_host_venv.ps1 -InstallDev
```

這個命令會檢查 Python 3.12+、建立 `.venv`，並將 `backend` 以 editable dev package 安裝進虛擬環境。不要直接使用系統 Python 執行專案測試或工具。

接著於專案根目錄安裝 Ollama 模型：

```powershell
ollama pull mistral:7b-instruct-v0.3-q4_K_M
ollama list
ollama show mistral:7b-instruct-v0.3-q4_K_M
ollama ps
```

由 `.env.example` 建立 `.env`，確認設定至少包含：

```env
LIVE_GAI_PROVIDER_ENABLED=true
GAI_EXECUTION_MODE=live
GAI_PROVIDER_NAME=ollama
GAI_PROVIDER_ENDPOINT=http://host.docker.internal:11434/api/chat
GAI_PROVIDER_MODEL=mistral:7b-instruct-v0.3-q4_K_M
GAI_PROMPT_TEMPLATE_VERSION=m6_ollama_action_v1
GAI_NUM_CTX=2048
GAI_TIMEOUT_MS=120000
GAI_MAX_RETRIES=1
GAI_BUDGET_MODE=auto
GAI_BUDGET_HARD_LIMIT=50000
GAI_KEEP_ALIVE=5m
GAI_SEED=114
```

若要在新電腦以 OpenAI 執行 M6，保留 Ollama 設定也可以，Run Settings 會選擇 provider；另外在 API container 使用同一份 `.env` 設定：

```env
OPENAI_API_KEY=你的伺服器端金鑰
OPENAI_API_ENDPOINT=https://api.openai.com/v1/responses
OPENAI_MODEL=gpt-5-nano-2025-08-07
```

`OPENAI_API_KEY` 不要放 frontend，也不要提交 portable package。Provider preflight 不產生 action；實際帳戶額度仍要在第一次 action call 才能確認。

啟動 Docker：

```powershell
docker compose build
docker compose up -d
```

執行環境驗證：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_portable_environment.ps1
```

驗證腳本會確認 `.venv\Scripts\python.exe` 存在且版本符合要求；若缺少，會要求先執行 bootstrap script。

若要由驗證腳本一併啟動 Docker：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_portable_environment.ps1 -StartServices
```

這個驗證腳本只做 health、模型清單、checksum 與 provider preflight，不會產生 GAI action。

若要確認 portable package 本身的程式也能通過測試，請在套件根目錄執行：

```powershell
$env:PYTHONPATH = "backend/src"
.\.venv\Scripts\python.exe -m unittest discover backend/src/two_stage/tests

cd frontend
pnpm install --frozen-lockfile
pnpm typecheck
pnpm build
cd ..
```

`pnpm install` 只需在該 portable package 第一次驗證或建置時執行；`node_modules` 與 `dist` 不應重新放回交付 ZIP。

`GAI_BUDGET_MODE=auto` 時，Preflight 會依目前選定的 topology、model、regime、trial、branch 與 M6 action-step upper bound 自動計算並凍結本次 budget。`GAI_BUDGET_HARD_LIMIT` 只是不允許超過的安全上限，不需要手動猜測完整 Run 的呼叫數。

目前執行參數的範圍為：`scenarios_per_regime = 1～300`、`trials / condition = 1～500`。兩者都提高時，M4 scenario 生成、M7 validation、storage 寫入與整體執行時間會增加；正式執行前應先以較小 trials 做 preflight／pilot。

## 5. Run 狀態監看與中斷恢復

按下執行後，前端會持續輪詢該 Run，畫面會顯示：

- `QUEUED`：等待背景 worker。
- `PREFLIGHT`／`FREEZING_INPUTS`：執行前檢查或凍結輸入。
- `RUNNING`／`RUNNING_LAMBDA_SWEEP`：目前正在執行。
- `MONITORING_RETRY`：暫時無法取得 API 狀態，前端會自動重試，不代表實驗停止。
- `CANCEL_REQUESTED`：已提出取消，等待目前 action step 結束。
- `SUCCEEDED`／`FAILED`／`CANCELLED`：Run 已進入終止狀態，狀態仍保留在畫面上。
- `INTERRUPTED_RESUMABLE`：API 或 worker 中斷，Run 可從 Run history 按 `Resume`。

瀏覽器重新整理後，前端會利用已保存的 active Run ID 重新連線監看。API 重啟後若原 worker 已不存在，系統會將長時間沒有更新的執行狀態標記為 `INTERRUPTED_RESUMABLE`，不會把它誤顯示成仍在執行，也不會自動發布半套 M8/M9 結果。

Run 進度與結果位置：

```text
storage/published/runs/<run_id>/run_progress.json
storage/published/runs/<run_id>/M0/～M9/
```

Resume 會沿用原本凍結的 Run 設定與 checkpoint；不要用新的 Run 取代中斷 Run，也不要刪除該 Run 的 storage 目錄。

## 6. 實驗執行順序

```text
API health
→ Data checksum verification
→ Ollama provider preflight
→ 2-call smoke
→ Topology／Regime Gate
→ 小型 exploratory
→ 完整正式 Run
```

每一階段都保留新的 `run_id`。不要把新電腦的 `storage` 與舊電腦結果混合後再比較；若要保存舊結果，另外複製成唯讀 archive。

Run 結果會保存於新電腦解壓後的專案目錄：

```text
storage/published/runs/<run_id>/
```

這個目錄透過 Docker Compose bind mount 對應到 API container 的 `/app/storage`。執行 `docker compose down` 不會刪除這些主機端檔案；不要使用會移除 volume 的清理命令來刪除結果。Portable package 交付時只有空白骨架，正式 Run 完成後再備份整個 `storage/published/runs`。

## 7. 速度比較

至少記錄：

- 單次 GAI action latency。
- 每分鐘完成 action 數。
- GPU 使用率與 VRAM 使用量。
- Ollama 是否把模型完整載入 GPU。
- 2-call smoke 時間。
- Topology／Regime Gate 時間。

估算方式：

```text
總時間 ≈ GAI action calls × 平均單次 latency + M0～M9 固定處理時間
```

先比較單工結果。只有確認單工穩定、VRAM 仍有餘裕後，才另行測試平行呼叫；不可直接提高 concurrency 來推估正式結果。

## 8. 可重現性與失敗判讀

新電腦必須保留：

- root seed。
- 正式 Data checksum。
- Ollama model tag 與 digest。
- prompt template version。
- `GAI_NUM_CTX`、timeout、retry、seed 與 budget 設定。

GAI `invalid_output` 或 `decision_infeasible` 是正式 `valid=0` 結果；Ollama 未啟動、GPU 不可用、timeout 或 budget 不足且沒有 terminal decision 時，維持 `unavailable`，不可轉成 0。

若前端顯示 `MONITORING_RETRY`，先等待自動重試；若最後變成 `INTERRUPTED_RESUMABLE`，確認 provider、Docker 與 API 後，再按 `Resume`。OpenAI 額度耗盡會顯示 `PARTIAL_QUOTA_EXHAUSTED`，已完成 paired results 會先發布，尚未完成的 trial 不會被算成 valid=0；額度恢復後可 Resume 同一個 Run。

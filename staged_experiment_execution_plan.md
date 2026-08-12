# 分階段實驗執行計畫

## 目的

本文件定義如何用同一套 M0-M9 orchestration 分階段驗證流程，控制 Gemini 成本與執行風險。它不改變 M1/M2 empirical residual、M5 observation、M7 independent validation 或 M8 指標公式。

## 共同設定

```text
root_seed = 114
scenarios_per_regime = 8
split = test
sampling = empirical residual with replacement
```

每一個 Run snapshot 會凍結：

```text
selected_rule_sources
selected_interfaces
selected_topology_ids
selected_model_ids
selected_regimes
trial_count_per_condition
run_purpose = development | exploratory | formal
```

`w/o` 使用 M4 `scenario_gt`；`w/` 使用 M5 由指定 model、paradigm、regime residual pool 產生的 `observed_population`。兩個 branch 的 M7 truth 都是 M4 `scenario_gt`。

## 1. Preflight

Preflight 是 read-only。它會確認 3 個 topology、5 個 eligible model、每個 `model x regime` residual pool 非空、M4 scenario checksum 可重現、M5 observation checksum 可重現、M7 truth lineage 與 Gemini provider/model/budget 狀態。它不呼叫 Gemini，並回報 planned calls。

## 2. Rule-based exploratory pilot

```text
human rules x rule-based
x 5 models x 3 topologies x 3 regimes
x 5 trials x (w/o, w/)
```

這是 45 個 condition cells、450 個 decision/validation branches，Gemini calls 為 0。它只驗證 M4-M9 流程、paired lineage、artifact checksum 與記憶體表現，不作論文結論。

## 3. GAI smoke

```text
1 topology x 1 model x 1 regime x 1 trial x (w/o, w/)
= 2 Gemini calls
```

先確認 structured JSON、M6 action schema、M7 independent validation、request/response checksum、API key redaction、retry/error classification。provider 未設定時，測試結果必須是 `unavailable`，不是 synthetic action 或 0。

## 4. GAI exploratory subset

建議先選 1 個 Density 與 1 個 Detection model：

```text
2 models x 3 topologies x 3 regimes x 10 trials x 2 branches
= 360 Gemini calls per rule source
```

若同時選人工與 AI-generated rule source，預估為 720 calls。這些結果標記 exploratory；除非另有完整統計設計，不宣稱 GAI 優於 Rule-based 或 AI 規則優於人工規則。

## 5. Rule-based formal experiment

主要論文結果使用：

```text
human rules x rule-based
x 5 models x 3 topologies x 3 regimes
x 30 trials x (w/o, w/)
= 2,700 decision/validation branches
= 0 Gemini calls
```

`R_ideal`、`R_deploy`、`Delta_R` 仍只由 M7 trial facts 與 M8 canonical aggregation 產生。5-10 trials 適合流程與 exploratory；正式主要結果至少 30 trials。若要更窄的信賴區間，可在同一 frozen config 上增加 trials，但不能把不同 config 混成同一個結果。

## 6. 發布規則

- `development`/`exploratory` 可保存 partial 或 unavailable，不能進入正式 Paper View。
- `formal` 若選定 GAI，所有必要 rule source/interface paired rows 必須完成；否則在 M8 寫入前停止。
- `unavailable`、provider failure、invalid output 永不轉成 0。
- 已完成的 GAI pair 以固定 pair key、input checksum 與 trace 支援 resume，不重跑成功 request。
- M7 永遠使用人工 gold-standard topology/rules 與 M4 `scenario_gt`，不讓 GAI 自我驗證。

## 7. 本次驗收紀錄

```text
Preflight: PASSED
scope: 3 topologies x 5 models x 3 regimes
pilot: 5 trials, 450 Rule-based branches, 0 Gemini calls
formal Run: decoupled-2-stage-20260804T152803390504Z-eb76dbcb
formal status: SUCCEEDED
formal condition_count: 15
formal available M8 rows: 90
formal unavailable reserved GAI rows: 90
formal executed_trial_count: 30
```

本次環境的 `gemini-2.5-flash-lite` provider 狀態為 `unavailable`，因此沒有送出 live Gemini request，也沒有把 unavailable 轉成 0。GAI structured-output smoke 僅驗證 unavailable/error path；取得 API key 後，仍需依序執行 2-call smoke 與 360/720-call exploratory Run。

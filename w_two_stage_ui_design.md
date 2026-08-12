# `w/ Two-stage framework` UI 呈現設計規格

> **目前實作狀態（2026-08-03）**：本文件早期章節保留較完整的 UI/研究檢查
> 設計草案，包含未實作的多頁、trial records、圖表與 Report Builder。現行
> 可操作 UI 是低資訊量的單頁流程，以下「目前已實作 UI」才是與程式一致的
> 介面基準；後續舊章節不得被解讀為目前已完成的功能。

## 1. 文件目的

本文件定義論文 **Table III 中 `w/ Two-stage framework` 實驗結果**在系統 UI 的呈現方式，並擴充支援：

- 5 個 Perception Models
- 3 個 Topology
- Low / Medium / High 三種 Density Regime
- Risk Consistency
- Action Consistency
- Invalid Output
- Rule Violation
- `R_deploy`
- `ΔR`

主要目標不是把所有結果一次塞入大表格，而是讓使用者可以：

1. 快速查看 15 個 Model–Topology 組合的整體結果。
2. 找出高風險組合。
3. 逐層查看 Density Regime、Scenario、Trial 與 Validator 結果。
4. 一鍵產生論文表格、圖表與完整實驗報告。
5. 保留論文正式名稱 **`w/ Two-stage framework`**，不可改名或省略。

## 1.1 目前已實作 UI

現行畫面順序為：

```text
Run Settings
→ Ideal Baseline
→ Deployment Comparison
→ Selected Configuration Detail
→ Paper View / Downloads
→ Advanced Information
```

Run Settings 可設定：

```text
root seed
trials / condition
scenarios / regime
Risk Consistency β
```

點擊「執行實驗」後，畫面只呈現正式的 5 models × 3 topologies deployment
matrix、LOW/MEDIUM/HIGH regime、deployment metrics、Ideal Baseline、
selected configuration detail 與 Paper View。Paper View 的 Framework、
Decision Interface、Regime 是獨立篩選器；`ALL` 只顯示所有符合條件的 canonical
rows，不做平均或重算。

Ideal Baseline 固定讀取 `w/o Two-stage framework + rule_based + ideal` 的
`R_ideal`，以 topology × regime 顯示；五個 model row 只做一致性檢查，不平均。
Deployment Comparison 固定讀取 `w/ Two-stage framework + rule_based`，不提供
`R_ideal` 或 `Valid Rate` 作為矩陣 metric。Selected Configuration Detail 顯示
`R_ideal`、`R_deploy`、`Delta R`、Risk/Action Consistency 與 failure breakdown。

目前 GAI 仍為 `unavailable`，null/空值不轉成 0。若 M4 或 M7 失敗，UI 清除上一個
成功 Run 的結果，顯示 failure message 與 diagnostics 下載入口。現行畫面不建立
另一套 M5 runner，也不把 M5 顯示為「fake error injection」；它是 controlled
empirical residual propagation。

以下原有 UI-1～UI-6 與圖表規劃屬於後續擴充參考，並非目前已完成的頁面清單。

---

## 2. 論文對應關係

論文 Table III 以以下分組呈現決策介面結果：

- `w/o Two-stage framework`
- `w/ Two-stage framework`

本文件只規劃 `w/ Two-stage framework` 的主要結果頁面。

在 `w/ Two-stage framework` 下，系統流程為：

```text
Perception Model
    ↓
M2 Regime-dependent Empirical Residual Distribution
    ↓
M4 Ground-truth Spatial Scenario
    ↓
M5 Controlled Empirical Residual Propagation
    ↓
M6 Decision Interface
    ↓
M7 External Validator
    ↓
M8 Reliability Metrics
    ↓
UI / Table / Figure / Report
```

核心資料關係：

```text
M4 scenario ground truth population
+ M2 sampled residual error
= M5 perturbed observation
```

驗證時，M7 Validator 必須使用 M4 ground truth，而不是使用 M5 perturbed observation 作為真值。

---

## 3. 實驗維度

### 3.1 第一層：Framework Condition

本頁固定為：

```text
w/ Two-stage framework
```

這是正式論文名稱，UI、報表、LaTeX 匯出與 CSV 欄位顯示名稱均應保留。

後端可使用：

```json
{
  "framework_condition": "with_two_stage",
  "framework_display_name": "w/ Two-stage framework"
}
```

### 3.2 第二層：Perception Model × Topology

```text
5 Perception Models × 3 Topologies = 15 configurations
```

例如：

| Configuration ID | Perception Model | Topology |
|---|---|---|
| C01 | P1 | T1 |
| C02 | P1 | T2 |
| C03 | P1 | T3 |
| C04 | P2 | T1 |
| ... | ... | ... |
| C15 | P5 | T3 |

UI 中應稱為：

- Experimental Configuration
- Model–Topology Configuration
- 實驗組合

不建議稱為「15 個類別」，避免與機器學習分類類別混淆。

### 3.3 第三層：Density Regime

每一個 Model–Topology 組合再細分為：

- Low
- Medium
- High

因此主要彙總條件數為：

```text
5 × 3 × 3 = 45
```

### 3.4 第四層：Scenario / Trial / Seed

每一個條件可以包含多次：

- Scenario
- Error Realization
- Trial
- Seed
- Decision Run
- Validation Run

UI 預設顯示彙總結果，需要時才展開 trial-level records。

---

## 4. UI 資訊架構

建議拆成六個頁面：

```text
UI-1  Experiment Runs
UI-2  w/ Two-stage Overview
UI-3  Configuration Detail
UI-4  Cross Comparison
UI-5  Raw Trial Records
UI-6  Report Builder
```

其中 `UI-2 w/ Two-stage Overview` 是主要入口。

---

# 5. UI-1：Experiment Runs

## 5.1 目的

讓使用者查看所有 `w/ Two-stage framework` 實驗執行情況，確認資料是否完整、是否成功、是否可進入結果頁。

## 5.2 畫面配置

```text
┌────────────────────────────────────────────────────────────────────┐
│ Experiment Runs                                      [建立新實驗] │
├────────────────────────────────────────────────────────────────────┤
│ Framework：[w/ Two-stage framework]                               │
│ Status：[All ▼]  Date：[All ▼]  Search：[Run ID / Tag]             │
├────────────────────────────────────────────────────────────────────┤
│ Run ID       Models  Topologies  Regimes  Trials  Status  Created │
│ EXP-001      5       3           3        30      SUCCESS  ...     │
│ EXP-002      5       3           3        50      RUNNING  ...     │
│ EXP-003      4       3           3        30      FAILED   ...     │
└────────────────────────────────────────────────────────────────────┘
```

## 5.3 表格欄位

| 欄位 | 說明 |
|---|---|
| Run ID | 實驗批次識別碼 |
| Framework | 固定顯示 `w/ Two-stage framework` |
| Models | 納入的 Perception Model 數量 |
| Topologies | 納入的 Topology 數量 |
| Regimes | Low / Medium / High 是否完整 |
| Scenario Count | 情境數 |
| Trial Count | 每組重複次數 |
| Seed Policy | 固定或多 seed |
| Status | PENDING / RUNNING / SUCCESS / FAILED |
| Preflight | PASS / WARNING / FAIL |
| Created At | 建立時間 |
| Actions | 查看、重跑、匯出、刪除 |

## 5.4 狀態規則

- `SUCCESS`：M2、M4、M5、M6、M7、M8 必要產物均完成。
- `WARNING`：部分組合 trial 數不足，但仍可查看結果。
- `FAILED`：關鍵產物缺失、checksum 不一致或 Validator 未完成。
- `RUNNING`：顯示進度條與目前執行模組。

---

# 6. UI-2：w/ Two-stage Overview

## 6.1 目的

用一個 5 × 3 Model–Topology 矩陣，快速呈現 15 個實驗組合在指定 Density Regime 與 Metric 下的結果。

## 6.2 頁首固定區

```text
w/ Two-stage framework
Run：EXP-20260730-001
Status：SUCCEEDED
Models：5
Topologies：3
Regimes：3
Trials per condition：30
Seed policy：fixed-seed set
```

右側操作：

```text
[重新執行]
[比較組合]
[產生圖表]
[匯出報告]
```

## 6.3 篩選列

```text
Density Regime：[Low ▼]
Metric：[R_deploy ▼]
Decision Interface：[All / Rule / GAI ▼]
Aggregation：[Mean ▼]
Confidence：[95% CI ▼]
Scenario Set：[All ▼]
```

必要規則：

- Framework 不提供修改，固定為 `w/ Two-stage framework`。
- Density Regime 可切換 Low / Medium / High / Overall。
- Metric 改變時，矩陣色階方向必須同步改變。

## 6.4 5 × 3 矩陣

```text
┌──────────────────────────────────────────────────────────────────┐
│ Metric：R_deploy            Regime：High                         │
├────────────────┬──────────────┬──────────────┬───────────────┤
│ Model          │ Topology T1  │ Topology T2  │ Topology T3   │
├────────────────┼──────────────┼──────────────┼───────────────┤
│ CSRNet         │ 0.910        │ 0.860        │ 0.790         │
│ YOLOv8         │ 0.870        │ 0.820        │ 0.730         │
│ Model-C        │ 0.940        │ 0.900        │ 0.850         │
│ Model-D        │ 0.840        │ 0.780        │ 0.680         │
│ Model-E        │ 0.920        │ 0.880        │ 0.810         │
└────────────────┴──────────────┴──────────────┴───────────────┘
```

## 6.5 矩陣格內容

每一格至少顯示：

```text
0.860
± 0.018
30 trials
```

必要時顯示狀態標籤：

```text
Stable
Warning
High Risk
Insufficient Trials
Validation Failed
```

建議格內資訊：

| 顯示項目 | 是否預設 |
|---|---|
| Metric Mean | 是 |
| Standard Deviation | 是 |
| Trial Count | 是 |
| 95% CI | Tooltip |
| ΔR | Tooltip |
| Rule Violation | Tooltip |
| Invalid Output | Tooltip |
| Configuration ID | Tooltip |

## 6.6 Tooltip

滑鼠停在 `CSRNet × T2` 時：

```text
Configuration：C02
Framework：w/ Two-stage framework
Perception Model：CSRNet
Topology：T2 Clustered Campus
Density Regime：High
R_deploy：0.860
ΔR：0.121
Risk Consistency：0.947
Action Consistency：0.921
Invalid Output：0.008
Rule Violation：0.143
Trials：30
95% CI：[0.842, 0.878]
```

## 6.7 色階規則

### 越高越好

- `R_deploy`
- Risk Consistency
- Action Consistency

### 越低越好

- `ΔR`
- Invalid Output
- Rule Violation

UI 必須根據指標方向自動顯示：

```text
↑ Higher is better
↓ Lower is better
```

不可所有指標都使用同一套色階判斷。

---

# 7. Overview 下方摘要區

矩陣下方顯示四張摘要卡。

```text
┌──────────────────┐ ┌──────────────────┐
│ Best Configuration│ │ Highest Risk     │
│ Model-C × T1      │ │ Model-D × T3     │
│ R_deploy 0.940    │ │ R_deploy 0.680   │
└──────────────────┘ └──────────────────┘

┌──────────────────┐ ┌──────────────────┐
│ Mean ΔR           │ │ Rule Violation   │
│ 0.114             │ │ 0.153            │
└──────────────────┘ └──────────────────┘
```

摘要文字範例：

```text
High Density 下，共有 4 個 Model–Topology 組合低於可靠度門檻 0.80。
最主要失敗來源為 capacity violation，而非 invalid output。
Topology T3 對 perception uncertainty 最敏感。
```

這些摘要可由規則產生，不必依賴生成式 AI。

---

# 8. UI-3：Configuration Detail

## 8.1 目的

點選矩陣中的某一格後，查看單一 Model × Topology 組合的完整結果。

例如：

```text
CSRNet × Topology T2
Framework：w/ Two-stage framework
Configuration ID：C02
```

## 8.2 分頁設計

```text
[Summary]
[Density Regimes]
[Decision Interface]
[Scenario Results]
[Residual Propagation]
[Validator Results]
[Artifacts & Lineage]
```

---

## 8.3 Summary 分頁

```text
Overall R_deploy        0.890
Reliability Drop ΔR     0.084
Risk Consistency        0.947
Action Consistency      0.932
Invalid Output Rate     0.008
Rule Violation Rate     0.112
```

下方顯示：

- Best regime
- Worst regime
- Main violation type
- Total scenarios
- Total trials
- Successful validation count
- Failed validation count

---

## 8.4 Density Regimes 分頁

| Regime | R_ideal | R_deploy | ΔR | Risk Consistency | Action Consistency | Invalid Output | Rule Violation |
|---|---:|---:|---:|---:|---:|---:|---:|
| Low | 0.980 | 0.950 | 0.030 | 0.970 | 0.960 | 0.000 | 0.040 |
| Medium | 0.970 | 0.890 | 0.080 | 0.940 | 0.930 | 0.005 | 0.110 |
| High | 0.950 | 0.780 | 0.170 | 0.880 | 0.840 | 0.020 | 0.230 |

表格上方顯示折線圖：

```text
X：Low / Medium / High
Y：R_deploy / ΔR / Rule Violation
```

用途：呈現密度提高後，感知誤差如何放大並影響部署可靠度。

---

## 8.5 Decision Interface 分頁

若系統同時執行 Rule-based 與 GAI，應在本頁比較，但 Framework 仍固定為 `w/ Two-stage framework`。

| Regime | Decision Interface | Risk Consistency | Action Consistency | Invalid Output | Rule Violation | R_deploy |
|---|---|---:|---:|---:|---:|---:|
| Low | Rule-based | 1.000 | 1.000 | 0.000 | 0.000 | 0.970 |
| Low | GAI | 0.912 | 0.903 | 0.010 | 0.080 | 0.890 |
| Medium | Rule-based | 1.000 | 1.000 | 0.000 | 0.000 | 0.940 |
| Medium | GAI | 0.901 | 0.872 | 0.020 | 0.150 | 0.810 |
| High | Rule-based | 1.000 | 1.000 | 0.000 | 0.000 | 0.880 |
| High | GAI | 0.842 | 0.801 | 0.050 | 0.270 | 0.690 |

顯示模式：

```text
[並排比較]
[只看 Rule-based]
[只看 GAI]
[差值模式]
```

注意：

- `w/ Two-stage framework` 是 Framework Condition。
- Rule-based / GAI 是 Decision Interface。
- 兩者不可混在同一欄位。

---

## 8.6 Scenario Results 分頁

| Scenario ID | Regime | D_total | Hot Zones | ρ | α | β | Trials | R_deploy | Status |
|---|---|---:|---|---:|---:|---:|---:|---:|---|
| S001 | Low | 120 | Z2,Z5 | 0.40 | 2.0 | 5.0 | 30 | 0.960 | PASS |
| S002 | Medium | 420 | Z3,Z4 | 0.55 | 2.5 | 3.0 | 30 | 0.890 | WARNING |
| S003 | High | 860 | Z1,Z6 | 0.70 | 4.0 | 2.0 | 30 | 0.760 | HIGH RISK |

點選 scenario 後，可以查看：

- Ground-truth population
- Perturbed observation
- Sampled residuals
- Decision action
- Validator violations

---

## 8.7 Residual Propagation 分頁

### 目的

讓使用者確認 `w/ Two-stage framework` 中誤差確實來自 M2 empirical residual pool，而不是任意 Gaussian noise。

### 摘要卡

```text
Residual Source：M2 empirical residual pool
Sampling Mode：with replacement
Negative Handling：clip-to-zero
Rounding Policy：nearest integer
Residual Regime：High
Residual Pool Size：48
```

### 表格

| Zone | Ground Truth d* | Sampled Residual ε | Raw Observed d_hat | Final Observed | Policy |
|---|---:|---:|---:|---:|---|
| Z1 | 120 | +18 | 138 | 138 | none |
| Z2 | 40 | -52 | -12 | 0 | clip-to-zero |
| Z3 | 85 | +9 | 94 | 94 | none |

### 必須顯示的公式

```text
d_hat = d* + ε^(r)
```

其中：

- `d*`：M4 ground truth
- `ε^(r)`：M2 regime-conditioned empirical residual
- `d_hat`：M5 perturbed observation

---

## 8.8 Validator Results 分頁

### 驗證分類

- Connectivity violation
- Capacity violation
- Hop violation
- Rule inconsistency
- Invalid output
- Schema invalid

### 表格

| Trial ID | Scenario | Interface | Valid | Connectivity | Capacity | Hop | Rule | Message |
|---|---|---|---|---:|---:|---:|---:|---|
| T001 | S003 | Rule | Yes | 0 | 0 | 0 | 0 | PASS |
| T002 | S003 | GAI | No | 0 | 1 | 0 | 1 | Z4 exceeds capacity |

必要治理規則：

```text
Validator Ground Truth Source = M4 scenario_gt
```

UI 必須顯示 checksum：

```text
M4 scenario_gt checksum：abc123...
M7 validator ground truth checksum：abc123...
Match：YES
```

若不一致，顯示紅色錯誤並禁止產生正式報表。

---

## 8.9 Artifacts & Lineage 分頁

| Module | Artifact | Version | Checksum | Status |
|---|---|---|---|---|
| M2 | residual_pool.parquet | v1 | ... | VERIFIED |
| M4 | scenario_gt.json | v1 | ... | VERIFIED |
| M5 | perturbed_observation.json | v1 | ... | VERIFIED |
| M6 | decision_action.json | v1 | ... | VERIFIED |
| M7 | validation_result.json | v1 | ... | VERIFIED |
| M8 | metrics_summary.json | v1 | ... | VERIFIED |

點擊 Artifact 可查看：

- Schema
- Lineage
- Created At
- Producer Version
- Input Checksums
- Download

---

# 9. UI-4：Cross Comparison

## 9.1 目的

讓使用者選擇 2～5 個 Model–Topology 組合進行比較。

```text
☑ CSRNet × T1
☑ CSRNet × T3
☑ YOLOv8 × T1
☐ Model-C × T2
```

## 9.2 比較表

| Configuration | Low R_deploy | Medium R_deploy | High R_deploy | Mean ΔR | Rule Violation |
|---|---:|---:|---:|---:|---:|
| CSRNet × T1 | 0.950 | 0.910 | 0.830 | 0.090 | 0.080 |
| CSRNet × T3 | 0.920 | 0.840 | 0.720 | 0.150 | 0.160 |
| YOLOv8 × T1 | 0.910 | 0.820 | 0.690 | 0.180 | 0.210 |

## 9.3 可回答問題

- 同一個 Perception Model 在不同 Topology 下差多少？
- 同一個 Topology 換不同 Perception Model 有何差異？
- 哪一個組合在 High Density 下最穩定？
- 哪一個 Topology 對 residual error 最敏感？

## 9.4 建議圖表

- Model × Topology heatmap
- Density regime line chart
- Rule violation grouped bar chart
- ΔR comparison chart
- Radar chart：只作輔助，不作論文主要結果

---

# 10. UI-5：Raw Trial Records

## 10.1 目的

提供研究者檢查 trial-level 資料，不作為一般使用者首頁。

## 10.2 表格欄位

| 欄位 | 說明 |
|---|---|
| Run ID | 實驗批次 |
| Configuration ID | Model–Topology 組合 |
| Model | Perception Model |
| Topology | Topology ID |
| Regime | Low / Medium / High |
| Scenario ID | 情境 |
| Trial ID | 重複實驗識別 |
| Seed | 隨機種子 |
| Residual Sample ID | M2 殘差樣本 |
| Observation Checksum | M5 輸入 |
| Decision Interface | Rule / GAI |
| Action Checksum | M6 輸出 |
| Validator Result | PASS / FAIL |
| Violation Type | 違規分類 |
| R_deploy Contribution | 0 / 1 |

## 10.3 操作功能

- 固定 Model、Topology 欄位
- 多欄排序
- 多條件篩選
- 顯示／隱藏欄位
- 只顯示 Failed Trials
- 只顯示 Capacity Violation
- CSV / JSON 匯出
- 查看完整 lineage

---

# 11. UI-6：Report Builder

## 11.1 目的

一鍵產生論文表格、圖表、附錄與系統報告。

## 11.2 報告設定

```text
Framework：w/ Two-stage framework
Run：EXP-20260730-001

主要指標：[R_deploy ▼]
數值格式：[Mean ± Std ▼]
小數位數：[3]
信賴區間：[95%]

包含：
☑ 15 組 Model–Topology 總覽
☑ Low / Medium / High 詳細表
☑ Rule-based vs GAI
☑ Residual Propagation 統計
☑ Validator Violation 統計
☑ 原始 Trial 附錄
☑ Artifact Lineage
```

按鈕：

```text
[預覽]
[產生論文表格]
[產生完整報告]
[匯出資料]
```

## 11.3 輸出項目

### Table III-style

保留論文的主要欄位：

| Framework | Regime | Risk Consistency | Action Consistency | Invalid Output | Rule Violation |
|---|---|---:|---:|---:|---:|
| w/ Two-stage framework | Low | ... | ... | ... | ... |
|  | Medium | ... | ... | ... | ... |
|  | High | ... | ... | ... | ... |

### 擴充表格

- Model × Topology overall matrix
- Density regime breakdown
- Rule-based vs GAI comparison
- Full 45-condition result table

### 圖表

- `R_deploy` heatmap
- `ΔR` heatmap
- Density regime degradation curve
- Rule violation comparison
- Invalid output comparison
- Error residual distribution

### 匯出格式

- CSV
- JSON
- Markdown
- LaTeX
- PNG
- SVG
- PDF

---

# 12. 論文檢視模式

UI 應提供一個「Paper View」切換：

```text
[Dashboard View] [Paper View]
```

## 12.1 Dashboard View

適合探索：

- Heatmap
- Summary cards
- Drill-down
- Filters
- Trial records

## 12.2 Paper View

完全依論文 Table III 的排版方式顯示：

```text
TABLE III
DECISION INTERFACE PERFORMANCE WITH THE PROPOSED TWO-STAGE FRAMEWORK
ACROSS DENSITY REGIMES
```

| Framework | Regime | Risk Consistency ↑ | Action Consistency ↑ | Invalid Output ↓ | Rule Violation ↓ |
|---|---|---:|---:|---:|---:|
| w/ Two-stage framework | Low | ... | ... | ... | ... |
|  | Medium | ... | ... | ... | ... |
|  | High | ... | ... | ... | ... |

可用下拉選單指定：

```text
Perception Model：[CSRNet ▼]
Topology：[T1 ▼]
Decision Interface：[All ▼]
```

這樣可在維持論文格式下，逐一輸出 15 個 Model–Topology 組合。

---

# 13. 首頁完整 Wireframe

```text
┌────────────────────────────────────────────────────────────────────────┐
│ w/ Two-stage framework                                  [Paper View]  │
│ Run EXP-001  SUCCEEDED  5 Models  3 Topologies  30 Trials             │
├────────────────────────────────────────────────────────────────────────┤
│ Regime [High ▼]  Metric [R_deploy ▼] Interface [All ▼] Mean [95% CI] │
│                                              [產生圖表] [匯出報告]    │
├────────────────────────────────────────────────────────────────────────┤
│                 T1                 T2                 T3               │
│ CSRNet          0.910              0.860              0.790            │
│ YOLOv8          0.870              0.820              0.730            │
│ Model-C         0.940              0.900              0.850            │
│ Model-D         0.840              0.780              0.680            │
│ Model-E         0.920              0.880              0.810            │
│                                                                        │
│ Legend：High reliability ───────────── Low reliability                 │
├────────────────────────────────────────────────────────────────────────┤
│ Best: Model-C × T1 │ Highest Risk: Model-D × T3 │ Mean ΔR: 0.114      │
├────────────────────────────────────────────────────────────────────────┤
│ Selected：CSRNet × T2                                                  │
│ [Summary] [Density] [Interface] [Scenario] [Injection] [Validator]    │
│                                                                        │
│ R_deploy 0.860   ΔR 0.121   Violation 0.143   Invalid 0.008           │
│                                                                        │
│ Low 0.95 ───── Medium 0.89 ───── High 0.78                            │
└────────────────────────────────────────────────────────────────────────┘
```

---

# 14. API 回傳資料設計

## 14.1 Overview API

```json
{
  "run_id": "EXP-20260730-001",
  "framework_condition": "with_two_stage",
  "framework_display_name": "w/ Two-stage framework",
  "density_regime": "high",
  "metric": "r_deploy",
  "aggregation": "mean",
  "configurations": [
    {
      "configuration_id": "C02",
      "perception_model": "CSRNet",
      "topology_id": "T2",
      "value": 0.86,
      "std": 0.018,
      "ci95_low": 0.842,
      "ci95_high": 0.878,
      "trial_count": 30,
      "risk_level": "warning"
    }
  ]
}
```

## 14.2 Detail API

```json
{
  "configuration_id": "C02",
  "framework_display_name": "w/ Two-stage framework",
  "perception_model": "CSRNet",
  "topology": {
    "id": "T2",
    "name": "Clustered Campus",
    "checksum": "topology_sha256"
  },
  "regime_metrics": [
    {
      "regime": "low",
      "r_ideal": 0.98,
      "r_deploy": 0.95,
      "delta_r": 0.03,
      "risk_consistency": 0.97,
      "action_consistency": 0.96,
      "invalid_output_rate": 0.0,
      "rule_violation_rate": 0.04
    }
  ]
}
```

---

# 15. 前端元件建議

```text
FrameworkHeader
RunStatusBar
ExperimentFilterBar
MetricSelector
RegimeSelector
ModelTopologyHeatmap
MetricSummaryCards
ConfigurationDrawer
DensityRegimeTable
DecisionInterfaceComparison
ScenarioTable
ErrorInjectionInspector
ValidatorInspector
ArtifactLineageTable
PaperViewTable
ReportBuilderDialog
```

---

# 16. 顯示規則與防誤解設計

## 16.1 必須保留名稱

正式顯示：

```text
w/ Two-stage framework
```

不可直接替換為：

- Perturbed
- Residual Propagation Mode
- Proposed Method
- Integrated Mode

上述詞彙只能作為 tooltip 補充說明。

## 16.2 不可將 Framework 與 Interface 混為一談

錯誤：

```text
Interface = w/ Two-stage framework
```

正確：

```text
Framework Condition = w/ Two-stage framework
Decision Interface = Rule-based / GAI
```

## 16.3 不可將 unavailable 顯示為 0

- 沒有產生結果：`Unavailable`
- 不適用：`Not Applicable`
- 模組不需要：`Not Required`
- 實際結果為零：`0.000`

這四種狀態必須分開。

## 16.4 不可隱藏 Trial Count

所有 mean、std、CI 都必須能追溯 trial count，避免小樣本結果被誤認為穩定結果。

## 16.5 必須顯示 Validator 真值來源

```text
Validator Ground Truth Source：M4 scenario_gt
```

不可只顯示「validated」，而不交代真值來源。

---

# 17. Preflight Gate

在允許正式產生論文表格前，必須通過：

```text
[PASS] 5 perception models available
[PASS] 3 topologies available
[PASS] Low / Medium / High residual pools available
[PASS] Minimum residual pool eligibility
[PASS] Scenario ground truth complete
[PASS] Observation checksum complete
[PASS] Rule and GAI use same observation checksum
[PASS] Validator uses M4 scenario_gt
[PASS] Trial count meets minimum requirement
[PASS] Metrics aggregation complete
```

若有失敗：

```text
Report publication blocked
```

但仍可允許使用者查看 debug 結果。

---

# 18. 權限建議

| 角色 | 權限 |
|---|---|
| Viewer | 查看結果、下載公開報表 |
| Researcher | 查看 trial、lineage、artifact |
| Experiment Operator | 建立、重跑、停止實驗 |
| Admin | 修改模型、Topology、門檻與報表治理規則 |

Locked Scientific Invariants 不應在一般 UI 開放修改，例如：

- Rule / GAI 必須使用相同 observation
- Validator 必須使用 M4 ground truth

---

# 19. 實作優先順序

## Phase 1：MVP

- Experiment Runs
- `w/ Two-stage framework` Header
- 5 × 3 Heatmap
- Regime Filter
- Metric Filter
- Configuration Detail
- Density Regime Table
- CSV Export

## Phase 2：研究檢查

- Scenario Results
- Residual Propagation Inspector
- Validator Results
- Artifact Lineage
- Trial-level Records
- Preflight Gate

## Phase 3：論文輸出

- Paper View
- LaTeX Table Export
- PNG / SVG Figures
- PDF Report
- Report Builder
- 自動產生 Table III-style 結果

---

# 20. Codex 實作指令摘要

```text
請新增一個固定以「w/ Two-stage framework」為主標題的實驗結果頁面。

第一層使用 5 個 Perception Models × 3 個 Topologies 的 5×3 heatmap，
使用者可選 Low、Medium、High、Overall density regime，並切換
R_deploy、Delta_R、Risk Consistency、Action Consistency、
Invalid Output、Rule Violation 指標。

點擊 heatmap 格後開啟 Configuration Detail，包含：
Summary、Density Regimes、Decision Interface、Scenario Results、
Residual Propagation、Validator Results、Artifacts & Lineage。

Framework Condition 與 Decision Interface 必須拆開：
framework_display_name 固定顯示「w/ Two-stage framework」，
decision_interface 才是 Rule-based 或 GAI。

Validator Results 必須顯示 ground truth source = M4 scenario_gt，
並檢查 M4 與 M7 ground truth checksum 一致。

沒有結果時顯示 Unavailable，不可顯示為 0。

新增 Paper View，以論文 Table III 的方式顯示：
Framework、Regime、Risk Consistency、Action Consistency、
Invalid Output、Rule Violation，並支援 CSV、JSON、Markdown、
LaTeX、PNG、SVG、PDF 匯出。
```

---

# 21. 驗收標準

- [ ] 頁面正式名稱顯示 `w/ Two-stage framework`。
- [ ] 5 × 3 矩陣正確呈現 15 個 Model–Topology 組合。
- [ ] 可切換 Low / Medium / High / Overall。
- [ ] 可切換六種以上主要指標。
- [ ] 指標高低方向顯示正確。
- [ ] 點擊矩陣格可查看詳細資料。
- [ ] Rule-based / GAI 與 Framework Condition 分開。
- [ ] Residual Propagation 可追溯到 M2 residual pool。
- [ ] Validator 明確使用 M4 scenario_gt。
- [ ] Unavailable 不會顯示為 0。
- [ ] Trial Count、Std、CI 可查看。
- [ ] Paper View 保留 Table III 結構。
- [ ] 可匯出 CSV、JSON、Markdown、LaTeX。
- [ ] Preflight 失敗時禁止產生正式論文報表。

---

## 22. 最終設計原則

`w/ Two-stage framework` UI 應遵循以下順序：

```text
w/ Two-stage framework
    ↓
5 × 3 Model–Topology Overview
    ↓
Low / Medium / High Density Regime
    ↓
Scenario / Trial / Error Realization
    ↓
Decision / Validator / Metrics
    ↓
Paper Table / Figure / Report
```

首頁負責快速定位高風險組合；詳細頁負責科學檢查與資料追溯；Paper View 負責忠實輸出論文 Table III 風格的結果。

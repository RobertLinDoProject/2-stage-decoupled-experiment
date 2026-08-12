# Paper View 閱讀指南

## 1. Paper View 在看什麼

Paper View 顯示目前選定的：

```text
topology × perception model × ground-truth regime
```

在 rule source comparison profile 中，後端仍保留八種 branch/interface 組合的 M8 canonical rows；Paper View 會將同一組條件的 ideal/deployment rows 合併成一列：

| Rule source | M6 interface | Framework |
|---|---|---|
| Human manual rules | Rule-based | w/o ↔ w/ |
| Human manual rules | GAI | w/o ↔ w/ |
| AI-generated rules | Rule-based | w/o ↔ w/ |
| AI-generated rules | GAI | w/o ↔ w/ |

每個 Paper View paired row 的 key 是：

```text
topology × perception model × ground-truth regime × rule source × decision interface
```

每列固定代表同一組 scenario/trial 的：

```text
w/o（M6 input = scenario_gt） ↔ w/（M6 input = observed_population）
```

因此 `實驗情境` 固定顯示 `w/o ↔ w/`，不是另一個可以單獨切換的 Framework 類別。Paper View 的 `ALL` 只顯示符合條件的所有 paired rows，不做平均、加總、跨 regime 合併或重新計算。

UI 上方另有兩張 `Ideal Baseline by Rule Source` 矩陣，並提供獨立的「決策方式」切換：

- `用 rule-based 做決策`
- `用 GAI 做決策`

兩張矩陣會同步切換目前的決策方式：

- `Human Rule Ideal Baseline`：`human_manual_v1 + 目前決策方式 + w/o + ideal`。
- `AI-generated Rule Ideal Baseline`：`ai_generated_derived_v1 + 目前決策方式 + w/o + ideal`。

兩張矩陣都以 topology × LOW/MEDIUM/HIGH 顯示 `R_ideal`，但規則來源不互相合併。Human baseline 是人工規則參考基準；AI baseline 是 AI rule source 在人工 M7 gold-standard 下的 ideal validity。新 Run 的 ideal baseline scope 是 `rule source × decision interface × topology × regime`，不包含 `perception model`：w/o 直接使用同一批 `scenario_gt`，因此每個 topology × regime × trial 只執行一個 ideal decision episode。M8 在各 model paired row 中重複攜帶這個共同值，是為了完整呈現 deployment pairing，不是五個獨立 baseline。

若載入舊 Run，可能仍看到不同 model 的 GAI ideal 值。這代表該 Run 產生於共用 ideal baseline 版本之前；UI 不平均、不覆寫歷史結果，而是在矩陣顯示 `Baseline consistency error`，並建議到 Paper View 查看各 model 的原始 paired rows。新 Run 則會以 manifest 中的 `ideal_baseline_scope`／`ideal_action_scope` 證明 model 維度已排除。這個 baseline 決策方式切換只影響兩張 baseline 矩陣，不會改變 Deployment Comparison Index、Selected Configuration Detail 或 Paper View。

因此完整的 baseline 維度是：人工規則／AI 生成規則 × Rule-based／GAI。若 GAI 沒有 terminal result，矩陣顯示 `unavailable`，不轉成 0；若 GAI 已執行但為 `invalid_output` 或 `decision_infeasible`，則保留 M8 的正式 `R_ideal = 0`，並依 M7／M6 evidence 解讀。

Deployment Comparison Index 另提供 `Rule Source` 與「決策」selector。它固定顯示目前選取 rule source 與決策方式的 `w/ + deployment`，用來快速查看人工／AI 規則及 rule-based／GAI 的 deployment 結果；完整 paired comparison 仍以 Paper View 為準。

## 2. 比較條件

### Rule Source

表示 M6 使用的 rule bundle：

- `Human manual rules`：人工建立的 topology/rules。
- `AI-generated rules`：由 AI topology graph materialize 的 rule bundle。

M7 不使用 AI rule source 自我驗證。M7 固定使用人工 topology/rules 與 M4 `scenario_gt` 作為共同 gold standard。

### Framework

- `w/o Two-stage framework`：ideal branch，M6 input 使用 `scenario_gt`，不注入 perception residual。
- `w/ Two-stage framework`：deployment branch，M6 input 使用由正式 residual 產生的 `observed_population`。

這裡的 `w/o` 與 `w/` 是兩種決策輸入情境，不代表 M7 是否執行。兩個 branch 都會由 M7 驗證。

### Trial Type

- `ideal`：對應 w/o branch。
- `deployment`：對應 w/ branch。

這個欄位是用來確認該列的 branch 語意，避免把 ideal 與 deployment 指標混讀。

### 決策方式（系統欄位：Decision Interface）

- `用 rule-based 做決策`：使用目前的 capacity-aware multi-source rule-based planner。
- `用 GAI 做決策`：使用 GAI decision adapter。

UI 執行設定中的欄位名稱是「決策」，Paper View 篩選器中的欄位名稱是「決策方式」。`ALL` 只代表同時顯示或執行兩種決策方式，不是第三種決策方法。

目前 GAI 可使用本地 Ollama；若 provider 未設定或 preflight 未通過，GAI row 會顯示 `unavailable`，不建立假 action、不填 0。新 Run 不會呼叫未核准的外部 provider。

### Regime

`LOW`、`MEDIUM`、`HIGH` 是 M4 產生 scenario 時的 ground-truth density regime。

Regime 用於 scenario generation、M2 residual pool 選擇與結果分組；不會直接乘上係數或扣分來製造可靠度趨勢。

## 3. Paper View 配對結果表格

Paper View 表格是配對結果的主要閱讀入口。它不再使用獨立的 `Paired Reliability Summary` 卡片；每個 paired condition 直接在同一列顯示：

```text
R_ideal
R_deploy
Delta R
```

在 rule source comparison profile 中，每個 `Topology × Model × Regime × Rule Source × 決策方式` 各有一列。上方的 Topology、Model、Regime 選擇器與 Rule Source、決策方式篩選器只控制顯示哪些列，彼此不會觸發聚合。

### R_ideal

```text
R_ideal = average(M7 valid of ideal trials)
```

表示在正確人數輸入下，該 rule source 與 M6 interface 產生的決策通過 M7 的比例。

### R_deploy

```text
R_deploy = average(M7 valid of deployment trials)
```

表示加入正式 perception residual 後，決策仍通過 M7 的比例。

### Delta R

```text
Delta R = R_ideal - R_deploy
```

`Delta R` 只在同一個 rule source、M6 interface、topology、model 與 regime 內比較 ideal/deployment 差異。三個欄位永遠顯示，不放入欄位 Checkbox。

- 越接近 `0`：ideal 與 deployment 的 valid rate 差距越小。
- 越大：deployment 相對於 ideal 的可靠度下降越明顯。

`Delta R` 不是 Risk Consistency、Action Consistency 或 regime penalty 的加權結果。

表格其他欄位可透過「Paper View 表格欄位」Checkbox 選擇。這些欄位不會改變三個 reliability 數值：

- `Risk 指標`：Risk Precision、Risk Recall、Risk Consistency、Risk β。
- `Action 指標`：Legality、Priority、Economy、Action Consistency。
- `Failure 指標`：Invalid Output、M6 Contract Violation、M6 Decision Infeasible、Rule、Capacity、Topology violation。
- `Executed Trials`：`ideal n / deployment n`。
- `M6 Outcome`：`ideal status / deployment status`。
- `Availability`：`ideal / deployment`。
- `Metric Policy`：該 paired row 使用的 M8 policy version。

Risk、Action、Failure 指標固定取 deployment branch，欄位名稱會標示 `deployment`。它們是診斷閱讀層，不會取代或重新計算 paired reliability。`R_ideal`、`R_deploy`、`Delta R` 直接讀取 M8 canonical row；前端只做 paired row 對齊與狀態檢查。

配對狀態規則如下：

- 完整且一致：顯示三個 M8 reliability 數值。
- 缺少 ideal 或 deployment，或任一 branch `unavailable`：三個欄位顯示 `unavailable`。
- paired canonical values 不一致：三個欄位顯示 `consistency error`，不補值、不平均。
- `invalid_output` 與 `decision_infeasible` 是已執行的 branch outcome，仍保留在 paired row 的 M6 Outcome 與 deployment diagnostics 中；它們不會被誤當成 `unavailable`。

## 4. Zero-headroom 情境

若出現：

```text
R_ideal = 0
R_deploy = 0
Delta R = 0
```

這個結果在數學上是正確的：

```text
Delta R = 0 - 0 = 0
```

但它不代表：

- AI rule source 表現良好。
- perception residual 沒有影響。
- ideal 與 deployment 都是可靠的。

正確解讀是：

> 該 rule source 在正確人數輸入下已全部未通過 M7，ideal branch 已經失敗，因此沒有可觀察的額外可靠度下降空間。

這種狀態在 UI 顯示為：

```text
Ideal baseline failed
Delta R has no interpretable headroom
```

原始 `R_ideal`、`R_deploy` 與 `Delta R` 仍保留，因為它們是 M8 canonical values。UI 額外顯示 interpretation status，避免把數值解讀成「沒有 perception degradation」。應回到 M7 violation evidence 查看 AI rule source 為何在 ideal branch 失敗。

若 `R_ideal = 0` 但 `R_deploy > 0`，也必須標示 `Ideal baseline failed`。這時可能出現負的 `Delta R`，但不能解讀成 perception 讓結果變好；它只表示 deployment branch 的部分輸出通過，而 ideal branch 本身已失敗，兩者沒有有效的完整 ideal reference point。

若：

```text
0 < R_ideal < 1
```

UI 顯示：

```text
Ideal baseline is partial
```

此時 `Delta R` 仍可作為條件性比較，但不能解讀成完整理想決策能力下的 deployment loss。

只有在 `R_ideal = 1` 時，才是完整通過的 ideal reference point。

## 5. 表格欄位閱讀方式

`R_ideal`、`R_deploy`、`Delta R` 是每列固定的主要 paired 結果，因此不再顯示 `Branch Valid Rate` 欄位。`Branch Valid Rate` 的意義已由兩個 branch-specific reliability 欄位取代：w/o branch 看 `R_ideal`，w/ branch 看 `R_deploy`。

其餘欄位使用 Checkbox 選擇是否顯示。Risk、Action、Failure 欄位都明確標示 `deployment`，代表它們是 w/ branch 的診斷，不是兩側重新平均的數字。`Executed Trials`、`M6 Outcome`、`Availability` 會以 `ideal / deployment` 並列，方便先確認配對是否完整。

## 6. Risk 指標

M7 先以 `scenario_gt` 判斷真正的高風險 source，再與 M6 建議疏散的 source 比較。

### Risk Precision

```text
Precision = TP / (TP + FP)
```

M6 建議疏散的來源中，有多少是真正高風險來源。高代表誤報較少。

### Risk Recall

```text
Recall = TP / (TP + FN)
```

真正高風險來源中，有多少被 M6 找出來。高代表漏掉的高風險來源較少。

### Risk Consistency

```text
F_beta = (1 + beta^2) × Precision × Recall
         / (beta^2 × Precision + Recall)
```

數值越高越好。`beta` 由 run config 的 `risk_f_beta` 提供；`beta > 1` 代表較重視 Recall。

Risk Consistency 不會被拿來計算 `R_deploy`。

### Risk beta

顯示計算 Risk Consistency 所使用的 `risk_f_beta`。它是計算設定，不是性能分數。

## 7. Action 指標

### Legality

檢查 action 是否符合必要的 M7 合法性規則，包括 source、target、合法 edge、capacity、source population 與 flow conservation。

通常以 trial-level legality score 聚合；越高越好。

### Priority

檢查 action 是否選擇 M6 規則排序中第一個可行的目的地。目的地排序會使用 topology rule 的成本與 deterministic tie-breaker。

越高代表越符合既定目的地優先順序。

### Economy

檢查同一 source 是否過度分散到太多 target：

```text
distinct_target_count <= 3 → 1
distinct_target_count > 3  → 0
```

越高代表分配較集中、較符合目前 economy rule。

### Action Consistency

```text
Action Consistency before gate
  = 0.50 × Legality
  + 0.35 × Priority
  + 0.15 × Economy
```

若發生 fatal legality failure，Action Consistency 會被設為 0。

它不是 action 與 ideal action 的相似度，也不是 `R_deploy`。

## 8. Failure Breakdown

以下欄位都是該類問題發生的 trial rate，數值越低越好。

### Invalid Output

M6 output 無法解析或缺少必要 action 欄位的比例，例如 source、target 或 move count 不完整。

### Rule Violation

該 trial 至少有一項必要 M7 validation flag 失敗的比例。

它是總括欄位，可能包含 invalid output、topology、capacity、source underflow 或 flow conservation violation。因此各 violation rate 不可直接相加，同一 trial 可能同時違反多項規則。

### Capacity Violation

以 M7 的 truth `scenario_gt` 計算 post-population 後，非出口節點超過 capacity 的比例。

### Topology Violation

action 的 source → target 不符合人工 gold-standard topology 或 allowed destination rule 的比例。

M7 可能另外保存 unknown target、forbidden target、source underflow 與 flow conservation 等 evidence；這些細節不一定各自成為 Paper View 的獨立欄位，但會反映在 Rule Violation 與 M7 evidence 中。

## 9. 其他欄位

### Executed Trials

該列實際執行並納入 M8 aggregation 的 trial 數量。它是樣本量，不是分數。

### Metric Policy

本次使用的 metric policy version，用來確認公式、aggregation 與 rounding policy。不同版本不應直接混合比較。

### Availability

- `available`：有正式 M6/M7/M8 結果。
- `unavailable`：沒有 terminal outcome，例如 GAI provider、budget 或 transport 未提供可驗證的 action。

`unavailable` 不等於 0，也不可當成失敗率 100%。

### M6 Outcome

Paper View 另外顯示 M6 的執行結果狀態：

- `available`：該 branch 的所有 trial 都有 terminal outcome；其中若有模型失敗，會由 paired row 的 `R_ideal`／`R_deploy` 與 failure rate 如實反映。
- `invalid_output`：GAI 已呼叫，但輸出不符合 canonical action contract，例如非法 target、錯誤 source 或 count 不符合上限；這些 trial 以 `valid=0` 並納入 M8 denominator。
- `decision_infeasible`：GAI 已執行，但 action episode 無法完成剩餘需求或找不到合法 target；這些 trial 以 `valid=0` 並納入 M8 denominator。
- `unavailable`：沒有可計入的 terminal decision，不計入 M8 denominator，指標維持 null。

`invalid_output`／`decision_infeasible` 是模型能力或決策 episode 的正式實驗結果；`unavailable` 是執行條件不足。兩者不可互換。

## 10. 三種比較方式

### w/o vs w/

固定 rule source、M6 interface、topology、model 與 regime，只比較 framework：

```text
w/o = scenario_gt
w/  = observed_population
```

主要比較：

- `R_ideal` 與 `R_deploy`
- `Delta R`
- paired row 的 `R_ideal`／`R_deploy`
- Capacity、Topology、Rule Violation

### Rule-based vs GAI

固定 rule source、topology、model、regime 與 framework，只比較 M6 interface。

GAI 的 provider 若可用，完整的 ideal/deployment terminal facts 會進入 M8；其中 `invalid_output`／`decision_infeasible` 會以 `valid=0` 納入數值比較。只有 `unavailable` 或未取得 terminal outcome 時，才不能拿來與 Rule-based 分數比較。

### Human rules vs AI-generated rules

固定 M6 interface、topology、model、regime 與 framework，只比較 rule source。

兩者共用 scenario、residual sample、observation 與 M7 human gold-standard，因此差異主要反映 M6 使用的 rule bundle 不同。

AI rule source 的 `R_ideal < 1` 代表即使人數正確，AI 規則產生的決策仍有部分無法通過人工 M7 驗證；它不是 Perception model accuracy。

## 11. 閱讀順序

主頁目前採用「一個區塊回答一個問題」的閱讀方式，建議依序閱讀：

1. **Ideal Baseline by Rule Source**：先看人工規則與 AI 生成規則在正確人數輸入下各自的決策能力。兩者是兩個獨立 baseline，不合併。
2. **Deployment Comparison Index**：再選擇 rule source 與決策方式，查看含 Perception 觀測的人數輸入下，選定 model、topology 與 regime 的快速結果矩陣。
3. **Selected Configuration Detail**：先看 `R_ideal`、`R_deploy`、`Delta R`；接著用 Consistency 判讀 M6 決策品質，最後用 Failure Breakdown 找出 M6/M7 的具體失敗類型。若有 invalid、infeasible 或 unavailable，則閱讀區上方的必要 Alert。
4. **Paper View**：最後用獨立條件與篩選器查看每組 `w/o ↔ w/` paired row；`R_ideal`、`R_deploy`、`Delta R` 直接在同一列比較，再依需要開啟 deployment diagnostics 欄位與下載資料。

主畫面預設只呈現主要結果；Risk、Action、Failure、Metric Policy 等欄位可以在進階區展開。這些欄位沒有被刪除，只是避免第一眼把不同聚合層級混在一起。

Paper View 的上方條件選擇器包含 Topology、Perception model、Regime；結果列篩選器包含 `Rule Source` 與 `決策方式`。Framework／Trial Type 不再作為獨立篩選，因為每列固定就是 `w/o ↔ w/`。表格下方的「Paper View 表格欄位」Checkbox 控制每列要顯示哪些可選欄位；`R_ideal`、`R_deploy`、`Delta R` 永遠顯示。後端與 artifact 仍保留 `framework_condition`、`trial_type` 兩個原始欄位；表格欄位分為核心結果、Risk、Action、Failure 與治理資訊五組，也可以使用「全部欄位」或「只看核心結果」快速切換。

若 paired summary status 是 `Ideal baseline failed`，先看 M7 violation evidence，不解讀 Delta R 為「沒有差異」。若 status 可解讀，再比較 R_ideal、R_deploy 與 Delta R。

Paper View 是 M8 canonical result 的閱讀層，不會在前端重新計算指標，也不會用其他條件的資料補入目前篩選結果。

### Selected Configuration Detail 的白話閱讀方式

這個區塊只描述目前選取的單一 `topology × Perception model × regime × rule source × 決策方式`，不代表整個 Run 的平均結果。建議依下列順序閱讀：

1. **Reliability Comparison**：`R_ideal` 是 M6 看到 `scenario_gt` 時的 M7 通過率；`R_deploy` 是 M6 看到 `observed_population` 時的 M7 通過率；`Delta R = R_ideal - R_deploy`，用來看觀測誤差進入 deployment 後的可靠度落差。
2. **Consistency**：`Risk Precision` 是 M6 標為高風險的來源中真正高風險的比例；`Risk Recall` 是真正高風險來源中被 M6 找到的比例；`Risk β` 是兩者的權重設定，`Risk Consistency` 是綜合結果。`Legality` 看 action 是否合法，`Priority` 看目標選擇是否符合優先順序，`Economy` 看是否避免不必要的分流，`Action Consistency` 是三者的綜合結果。這些是診斷指標，不會取代 `R_deploy`。
3. **Failure Breakdown**：`Invalid Output`、`M6 Contract Violation`、`M6 Decision Infeasible` 描述 M6 輸出或分配失敗；`Rule Violation`、`Capacity Violation`、`Topology Violation` 描述 M7 對 action 的驗證結果。每個 rate 都是該類問題在 executed trials 中的比例，不是要互相相加的總分。

若出現 `invalid_output`、`decision_infeasible` 或 `unavailable`，畫面會在主要內容上方保留必要 Alert；這些狀態不會在 Selected Configuration Detail 中另外建立三欄狀態卡，也不會把 unavailable 當成 0。

### Perception Error Tolerance Boundary

Selected Configuration Detail 的 Boundary 區塊提供兩種不同層級的資訊：

1. `Observed Estimate`：直接讀取既有 Run 的 M5 observation 與 M7 trial facts，不重跑實驗。它描述目前成功與失敗 trial 實際觀察到的誤差範圍，不能當作精確臨界值。
2. `Computed Boundary`：使用者按下「開始 Boundary Sweep」後才執行。它固定使用同一批 scenario 與 residual，以 `lambda=0.00～1.00、step=0.05` 重跑 Rule-based M5→M6→M7，產生可靠度曲線與誤差容忍目標。

計算方式為：

```text
observed_population(lambda)
  = max(0, round_half_up(scenario_gt + lambda × sampled_residual))
```

`lambda=0` 代表正確人數，`lambda=1` 代表正式 M5 observation。Boundary Sweep 不重新抽 residual、不合成 Gaussian、不修改正式 M8/M9，也不呼叫 GAI。GAI 若有保留 trial facts，可以讀取 Existing Estimate；v1 不做 GAI replay sweep。

Sweep 會檢查 `R_deploy > 0`、`>=0.50`、`>=0.80`、`>=0.95`。`safe_critical_lambda` 表示從 lambda=0 開始，直到該點以前每個已測試點都達標的最大值；需要降低的數值是 residual magnitude reduction，不是模型 accuracy 提升。`ABOVE_SEARCH_RANGE` 表示 lambda=1 仍達標；`NOT_REACHED` 表示目前範圍沒有找到安全邊界；`NON_MONOTONIC_RELIABILITY_CURVE` 表示曲線不是單調下降，應搭配 trial evidence 解讀。若 `R_ideal=0`，顯示 `BASELINE_FAILED`，因為正確人數輸入本身已失敗，不能把改善直接歸因於 perception。

Boundary 結果保存於來源 Run 的獨立 `boundary_analysis/{boundary_job_id}` 目錄，並標示：

```text
COUNTERFACTUAL ANALYSIS — NOT FORMAL M8/M9 RESULT
```

畫面中的數值直接讀取 M8 canonical row；單一 scenario/trial 的實際人流、action、節點與 M7 evidence，請使用 topology preview 查看。

## 9. Run 完成後的 Insight 報告

每個成功完成的 Decoupled Run 會在 M9 產生：

- `insight_report.md`：給人閱讀的白話結果整理。
- `insight_summary.json`：給 UI 或分析程式使用的結構化摘要。

報告固定按照以下來源產生：

```text
M8 canonical metrics
→ M9 all tables row-count check
→ M7 trial validation evidence
→ M6 GAI trace
→ insight 結論
```

M8 是可靠度數值的唯一權威來源；M7 只用來說明失敗與 violation evidence，M6 trace 只用來說明 GAI 呼叫狀態。報告不會改寫 M8、不會把 unavailable 轉成 0，也不會把描述性趨勢自動解讀成因果關係。

## 12. Perception Error Boundary

`Perception Error Boundary` 是針對目前選定的 `Topology × Model × Regime × Rule Source × 決策方式` 所做的唯讀 counterfactual sensitivity analysis。它不修改 M5 observation、M7 trial facts、M8/M9 artifact，也不是正式論文結果。

### Analytical boundary

這一層只回答 high-risk 判定與 requested move count 的數學門檻。若 source capacity 為 `C`、M6 risk threshold 為 `rho`，則：

```text
first_high_risk_count = ceil(rho × C)
last_non_high_count = first_high_risk_count - 1
signed_error_boundary = last_non_high_count - ground_truth_population
```

它表示人口估計低估或高估到哪裡會讓 M6 改變 high-risk 分類；不代表 M7 一定通過。M7 仍會檢查容量、拓撲、來源流量與流量守恆。

### Existing Run Analysis 與 Computed Boundary

系統只使用已保存的 M5 empirical residual，不重新抽樣、不合成 Gaussian、不建立 synthetic noise。開啟功能時先顯示 `Observed Estimate`，直接讀取 M5 observation 與 M7 trial facts，呈現既有 R_deploy、MAE、P90、最大誤差與成功／失敗誤差區間。這是觀察範圍，不是精確臨界值。

使用者按下「開始 Boundary Sweep」後，才執行 `Computed Boundary`。`lambda` 是 residual magnitude scale：

```text
alpha = 0  → observed_population = scenario_gt
alpha = 1  → observed_population = formal M5 observation
observed_alpha = max(0, round_half_up(scenario_gt + sampled_residual × alpha))
```

v1 固定使用 `lambda=0.00～1.00、step=0.05`，每個點使用相同 scenario、residual、seed 與 M7 human gold-standard validator 產生 counterfactual valid rate。固定目標為 `R_deploy > 0`、`>=0.50`、`>=0.80`、`>=0.95`。`safe_critical_lambda` 要求從 lambda=0 到該點以前每個已測試點都達標；所需降低的是 residual magnitude，不是 model accuracy。lambda=1 仍達標時標示 `ABOVE_SEARCH_RANGE`，曲線上升時標示 `NON_MONOTONIC_RELIABILITY_CURVE`。

若粗略曲線首次觀察到 `R_deploy=0`，畫面會優先顯示 `focus curve`：從 `lambda=0.00` 到第一個零可靠度點、step `0.01` 的細部結果；完整 `0.00～1.00、step=0.05` 曲線可展開查看。Focus curve 用來細看臨界區間，不會刪除後續結果，也不保證第一個零點就是精確臨界值。

若 `R_ideal = 0`，畫面會顯示 `Ideal baseline failed — perception boundary is not interpretable`；此時正確人口輸入本身已無法通過 M7，不能把改善量歸因於 perception error。

### 讀取限制

- Rule-based 有完整 M5/M7 lineage 時可執行 Computed Boundary。
- GAI 可讀取既有 trial facts 做 Existing Run Analysis；v1 不重呼叫 GAI 做 Computed Boundary，顯示 `Computed Boundary unavailable — GAI replay is not enabled`。
- 下載的 JSON/Markdown 會標示 `COUNTERFACTUAL ANALYSIS — NOT FORMAL M8/M9 RESULT`。
- `alpha=0`、`alpha=1` 與正式結果的一致性只用來驗證 lineage；不會覆寫正式 metric。

## 13. Local Ollama GAI 結果

目前 M6 GAI adapter 使用本機 Ollama 的 `mistral:7b-instruct-v0.3-q4_K_M`。它不是 plan selector，也不會從系統預先給它多個完整 plans 選擇；每個 action step 只收到當下 branch-visible population、合法 target、target `max_count` 與成本，並直接產生一筆 canonical action JSON：

```json
{"action_id":"A-0001","from_node":"11","to_node":"8","count":600}
```

系統只驗證 action contract，不修補模型答案、不換 target、不用 Rule-based fallback。每次成功 action 才更新 shared remaining capacity，完整 episode 再交給 M7。M7 一律使用人工 topology/rules 與 M4 `scenario_gt` 驗證；Mistral 不取得 M7 truth、M8 metrics 或 residual truth label。

Run Settings 會顯示 provider、model、prompt version、context size、budget 與 local call status。長時間 GAI Run 以背景方式執行，Run Detail 顯示目前 stage、action episode、completed calls、invalid output 與 transport failure；可取消或以相同 frozen config resume。API key 不需要，Ollama endpoint 只允許 local host / `host.docker.internal`。

GAI row 的狀態區分為：

- `available`：ideal/deployment action episodes 都完成，且 M7 facts 可聚合。
- `invalid_output`：模型有回應但不符合 canonical M6 contract；trial fact 的 `valid=0`、`invalid_output=1`，會計入 M8。
- `decision_infeasible`：模型有回應但無法完成合法分配；trial fact 的 `valid=0`、`m6_decision_infeasible=1`，會計入 M8。
- `unavailable`：provider、budget 或 transport 沒有產生可驗證的 terminal decision；不計入 M8，指標維持 null。

只要 ideal/deployment 兩邊都有 terminal outcome，paired group 就是 `available`，即使數值為 0；`R_ideal`、`R_deploy`、`Delta_R` 仍由 M8 原公式產生。只有 `unavailable` 才顯示 null/unavailable，不使用 Rule-based 結果替代。

## 14. OpenAI provider 與部分完成 Run

完整 M8/M9 會在所有必要 ideal/deployment episodes 完成後發布；若 OpenAI 額度先耗盡，則先發布標示為 `PARTIAL_QUOTA_EXHAUSTED` 的 partial M8/M9 artifact。Paper View 可勾選 `Run Status`、`Expected Trials`、`Executed Trials`、`Paired Completed Trials` 與 `Completion`，分別查看預計 trials、已完成 terminal outcome、兩側都完成的 paired trials，以及 complete／partial／incomplete 狀態。尚未執行的 trial 不會被填成 0。

Run Settings 選擇 `OpenAI · GPT-5 Nano` 時，M6 使用 OpenAI Responses API 產生與 Ollama 相同的單筆 canonical action。API key 只由 API／Worker container 讀取；Paper View、M6 trace、M9 manifest 與下載檔案都不顯示 key。Provider preflight 只檢查 key 與 model access，不代表帳戶尚有足夠額度。

若實際 action 呼叫回傳 `insufficient_quota` 或 billing hard limit，Run 狀態會變成 `PARTIAL_QUOTA_EXHAUSTED`，而不是把整個 Run 當成一般 FAILED。畫面會保留已完成的 paired rows，並顯示「部分完成、可恢復」；尚未執行的 trial 顯示 `incomplete`／`unavailable`，不填成 0。`R_ideal`、`R_deploy`、`Delta_R` 只使用同一批已完成 ideal/deployment pair，因此 partial row 的 denominator 可能小於原設定的 trials。

額度恢復後按 `Resume`，系統沿用同一個 Run ID、scenario、observation、seed、prompt version 與 input checksum，讀取 `gai_action_journal.jsonl`，只補尚未完成的 action step。若模型已回覆但 action contract 錯誤，狀態是 `invalid_output` 並以 valid=0 納入 M8；若 provider 沒有可用回覆，才是 `unavailable`，維持 null。

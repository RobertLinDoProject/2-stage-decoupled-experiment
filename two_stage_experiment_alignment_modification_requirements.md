# Decoupled Two-Stage Experiment 系統修改需求

> **目前實作對齊註記（2026-08-03）**：本文件保留原始研究修改需求；目前可執行
> 系統的實際 contract 以 `codex_two_stage_ui_system_build_spec.md`、
> `decoupled_2_stage_experiment_calculation_flow.md` 與
> `contracts/decoupled_2_stage_experiment_contracts.md` 為準。以下 M4/M6/M7
> 補充已納入目前實作：M4 只接受通過 M6 feasibility preflight 的 candidate，
> M6 使用 capacity-aware multi-source planner，M7 維持 scenario_gt truth isolation。

## 0. 文件目的

本文件是實驗系統的正式修改規格。修改後的系統必須使用一致、可追溯、可重現的實驗定義產生結果，不得再以未經核准的替代公式、額外修正策略或自行推測的規則取代本文件所列定義。

本次修改範圍涵蓋：

- 實驗條件定義
- Ideal 與 deployment 分支
- External validation
- Risk Consistency
- Action Consistency
- Reliability metrics
- Scenario generation
- M0、M4、M5、M6、M7、M8、M9 的責任
- API、UI、輸出表格與測試

GAI 介面維持 `unavailable`，不納入本次正式計算修改。

---

# 1. 不可違反的總體原則

## 1.1 不得自行新增實驗方法

系統只能實作本文件列出的實驗條件、公式與欄位。

不得：

- 自行增加額外誤差緩衝或決策修正策略。
- 自行改變指標權重。
- 自行改變 Risk Consistency 的比較集合。
- 自行將多個不同意義的指標混合成新的 reliability 分數。
- 在缺少設定值時，自動猜測實驗參數。
- 將 unavailable、not executed 或 missing data 轉成 0。
- 使用 observation 作為 validation ground truth。

## 1.2 所有正式結果必須可追溯

每一個 aggregated result 必須能回溯到：

```text
run_id
condition_id
trial_id
pair_id
topology_id
model_id
regime
scenario_id
root_seed
framework_condition
decision_interface
metric_policy_version
```

任何會改變實驗結果的參數，都必須寫入 run manifest 與結果 artifact。

## 1.3 相同比較條件必須配對

同一組比較中的 ideal 與 deployment trial 必須共用：

```text
相同 topology
相同 scenario_gt
相同 scenario_id
相同 decision policy
相同 validator
相同規則與容量
相同 root_seed 對應的實驗配對
```

兩者只能在 decision input 是否包含 perception residual 上不同。

---

# 2. 正式實驗條件

系統必須保留兩個正式 framework conditions。

## 2.1 `w/o Two-stage framework`

此條件是 ideal baseline。

決策輸入必須直接使用：

```text
decision_population = scenario_gt_population
```

此條件不得：

- 使用 perception residual。
- 使用 observation。
- 使用任何額外 correction 或 mitigation。

決策流程：

```text
scenario_gt
→ Rule-based decision
→ External validator 使用 scenario_gt
→ ideal trial metrics
```

## 2.2 `w/ Two-stage framework`

此條件是 controlled empirical residual propagation 後的 deployment evaluation。

每個 source node 的 observation：

```text
observed_population[node]
    = max(
        0,
        round_half_up(
            scenario_gt_population[node]
            + sampled_residual[node]
        )
      )
```

決策輸入必須使用：

```text
decision_population = observed_population
```

決策流程：

```text
scenario_gt
+ model/regime empirical residual
→ observation
→ Rule-based decision
→ External validator 使用 scenario_gt
→ deployment trial metrics
```

## 2.3 Condition 配對要求

每一個 deployment trial 都必須有對應的 ideal trial：

```text
pair_id 相同
scenario_id 相同
scenario_gt checksum 相同
topology checksum 相同
capacity checksum 相同
```

輸出必須明確包含：

```text
trial_type = ideal | deployment
pair_id
```

---

# 3. Perception residual 與 controlled empirical residual propagation

## 3.1 Residual 定義

```text
residual = predicted_count - ground_truth_count
```

Residual 必須保留正負號：

```text
residual > 0：高估
residual < 0：低估
residual = 0：無誤差
```

## 3.2 Residual pool

Residual pool 必須依以下條件分組：

```text
model_id + ground_truth_regime
```

不得使用 `predicted_regime` 建立正式 residual pool。

每個 pool 必須保存完整 empirical residual samples，不得只保存平均值或標準差後重新合成噪音。

## 3.3 Residual sampling

每個 deployment trial 必須記錄：

```text
residual_pool_id
residual_pool_count
sampling_policy
sampling_seed
sampled_residuals
```

Residual sampling 必須由 M0 的明確設定控制，不得在不同模組中各自使用未記錄的亂數來源。

## 3.4 Validation ground truth

External validator 一律使用：

```text
scenario_gt
```

不得使用：

```text
observed_population
decision_population
candidate internal state
```

作為合法性、容量與規則驗證的真值。

---

# 4. Scenario generation

## 4.1 Ground-truth scenario 定義

每個 scenario 必須產生：

```text
d* = (d1*, d2*, ..., dN*)
```

並滿足：

```text
每個 node 的 population >= 0
每個非出口 node 的 population <= capacity
所有 node population 加總 = D_total
```

## 4.2 Scenario 控制參數

Scenario generation 必須明確使用並保存：

```text
D_total
hotspot node subset H
hotspot population ratio rho
scenario_alpha
scenario_beta
scenario_seed
```

其中 hotspot ratio 必須由實際 scenario 驗證：

```text
rho_actual
    = sum(population of nodes in H)
      / sum(population of all nodes)
```

## 4.3 Zone allocation

Zone allocation 權重必須由設定的 Beta distribution 產生：

```text
weight_i ~ Beta(scenario_alpha, scenario_beta)
```

再依 `D_total`、容量與 hotspot ratio 分配到各 node。

不得以未登錄的均勻亂數或其他權重公式取代。

## 4.4 Scenario artifact

每個 scenario 必須保存：

```text
scenario_id
topology_id
regime
D_total
H
rho_requested
rho_actual
scenario_alpha
scenario_beta
scenario_seed
scenario_seed_base
generation_attempt
candidate_rejection_count
candidate_rejection_reason_counts
scenario_gt_population
capacity_check_passed
decision_feasibility_status
decision_feasibility_reasons
scenario_policy_id
scenario_policy_version
M6 decision policy version
scenario_checksum
```

每個 topology × regime 仍須產生固定數量的正式 scenarios。正式 scenario
之前的 candidate 若未通過 M6 feasibility，必須以 deterministic candidate seed
重抽，最多 512 次；拒絕資料只進入
`M4/scenario_generation_diagnostics.json`，不進入正式 scenario、M5 或 M8。
若超過上限仍無可行 candidate，M4 Failed。

若必要設定缺失，preflight 必須失敗，不得自動補猜值。

---

# 5. Rule-based decision

Rule-based decision 必須對輸入的 `decision_population` 產生 evacuation actions。

每筆 action 至少包含：

```text
source_id
target_id
move_count
priority_metadata
```

Rule-based decision 不得讀取 `scenario_gt` 來偷偷修正 deployment decision；`scenario_gt` 只能供 ideal branch 與 external validator 使用。

Decision output 必須經過 schema validation 後，才可送入 external validator。

目前正式 planner policy 為：

```text
capacity_aware_multi_source_rule_based v1.0.0
```

ideal 與 deployment 使用同一個 planner；ideal 的
`decision_population = scenario_gt`，deployment 的
`decision_population = observation`。source 依 utilization、requested move
count、natural node id 排序，target 依 total cost 與 node id 排序，並追蹤
非出口 target 的 shared remaining capacity。`priority_metadata` 必須保存
requested/allocated quantity、selected rank、target remaining capacity 與
allocation order。M6 不讀 M7，也不使用 scenario_gt 修正 deployment action。

---

# 6. External validator

## 6.1 Validator 輸入

```text
V(action, topology, scenario_gt)
```

Validator 必須獨立於 decision generator，不得以 decision generator 自己的判定結果代替 validation。

## 6.2 必要檢查

每個 trial 至少必須產生：

```text
invalid_output
topology_violation
unknown_target_violation
forbidden_target_violation
capacity_violation
source_underflow_violation
flow_conservation_violation
rule_violation
valid
violation_reasons
```

## 6.3 Post-state population

所有非出口 node 必須統一計算：

```text
post_population[node]
    = scenario_gt_population[node]
    + incoming[node]
    - outgoing[node]
```

並驗證：

```text
post_population[node] >= 0
post_population[node] <= capacity[node]
```

不得只檢查 target 的 `scenario_gt + incoming`，而忽略同一 node 的 outgoing。

## 6.4 `valid` 與 `rule_violation`

只要任何必要 validation 失敗：

```text
valid = 0
rule_violation = 1
```

全部通過時：

```text
valid = 1
rule_violation = 0
```

`violation_reasons` 必須列出實際原因與相關 node/action，不得只有單一布林值。

---

# 7. Risk Consistency

## 7.1 指標目的

Risk Consistency 衡量：

```text
真正需要疏散的來源區域
與
實際收到疏散建議的來源區域
之間的一致程度
```

## 7.2 Ground-truth expected sources

使用 `scenario_gt` 與正式 risk threshold 判斷：

```text
expected_high_sources
    = {
        source |
        scenario_gt_population[source] / capacity[source]
        >= risk_threshold
      }
```

## 7.3 Recommended sources

從實際 decision output 取得：

```text
recommended_sources
    = {
        source |
        該 source 至少有一筆有效格式的 evacuation action
      }
```

不得只使用 decision module 內部的風險標記代替 `recommended_sources`。

## 7.4 TP、FP、FN

```text
TP = count(expected_high_sources ∩ recommended_sources)
FP = count(recommended_sources - expected_high_sources)
FN = count(expected_high_sources - recommended_sources)
```

## 7.5 Precision 與 Recall

```text
risk_precision = TP / (TP + FP)
risk_recall    = TP / (TP + FN)
```

## 7.6 F-beta

Risk Consistency 必須使用可配置的 F-beta：

```text
risk_consistency
    = (1 + beta^2) * precision * recall
      / (beta^2 * precision + recall)
```

系統正式參數名稱：

```text
risk_f_beta
```

預設值：

```text
risk_f_beta = 2.0
```

參數限制：

```text
risk_f_beta > 0
```

不得在程式中把 `2` 寫死於公式。

## 7.7 邊界條件

必須固定為：

| expected_high_sources | recommended_sources | 結果 |
|---|---|---:|
| 空 | 空 | 1.0 |
| 非空 | 空 | 0.0 |
| 空 | 非空 | 0.0 |
| 非空 | 非空 | 正常計算 F-beta |

## 7.8 Trial-level 輸出

```text
expected_high_sources
recommended_sources
risk_tp
risk_fp
risk_fn
risk_precision
risk_recall
risk_f_beta
risk_consistency
```

---

# 8. Risk F-beta 的 M0 與 UI 設定

## 8.1 M0 config

M0 必須新增：

```yaml
metrics:
  risk_consistency:
    formula: f_beta
    beta: 2.0
```

Run 建立時，beta 必須寫入 immutable run config。

## 8.2 API

Run create/request DTO 必須接受：

```json
{
  "risk_f_beta": 2.0
}
```

後端驗證：

```text
必填或使用正式預設值 2.0
必須為數值
必須大於 0
```

API response 與 run detail 必須回傳實際使用的 beta。

## 8.3 UI

實驗設定頁必須提供：

```text
欄位名稱：Risk Consistency β
元件：數值輸入欄位
預設值：2.0
限制：必須大於 0
```

UI 必須同時顯示：

```text
目前 beta 值
Precision
Recall
Risk Consistency
```

Tooltip 說明應指出：

```text
beta > 1 時提高 Recall 的影響；
beta < 1 時提高 Precision 的影響；
beta = 1 時等同 F1。
```

使用者修改 beta 後：

- 必須建立新的 run 或重新計算 metrics。
- 不得直接修改既有 run 的結果。
- 新 beta 必須寫入 run manifest、M8 artifact、M9 表格與 UI。

## 8.4 Beta 名稱避免衝突

Risk 指標參數必須命名為：

```text
risk_f_beta
```

Scenario generator 的 Beta distribution 參數必須命名為：

```text
scenario_alpha
scenario_beta
```

不得只使用模糊欄位名 `beta`。

---

# 9. Action Consistency

## 9.1 正式公式

Action Consistency 必須由以下三個 component 組成：

```text
action_consistency_before_gate
    = 0.50 * legality_score
    + 0.35 * priority_score
    + 0.15 * economy_score
```

權重固定為：

```text
Legality = 0.50
Priority = 0.35
Economy  = 0.15
```

不得由 UI 任意修改，也不得在不同 topology 或 model 使用不同權重。

## 9.2 Legality score

Legality 必須由 external validator 結果產生。

第一版正式定義：

```text
全部必要 validation 通過：legality_score = 1
任一必要 validation 失敗：legality_score = 0
```

必要 failure 至少包含：

```text
invalid output
unknown target
forbidden target
topology violation
capacity violation
source underflow
flow conservation violation
非法 move_count
```

## 9.3 Fatal legality gate

只要：

```text
legality_score = 0
```

則：

```text
action_consistency = 0
fatal_legality_gate = true
```

不得讓 Priority 或 Economy 抵銷非法決策。

合法時：

```text
fatal_legality_gate = false
action_consistency = action_consistency_before_gate
```

## 9.4 Priority score

Priority 必須直接使用系統既有 topology 優先順序資料與實際選擇結果計算。

本次修改不得重新定義另一套 topology 路徑演算法。

M7 必須輸出：

```text
priority_score
priority_evidence
```

`priority_evidence` 至少要能追溯：

```text
source
可行目的地排序
實際選擇目的地
對應 priority 判定
```

不得只輸出一個無法解釋的分數。

## 9.5 Economy score

每個 source 的 distinct target 數量必須先去重。

正式規則：

```text
distinct_target_count <= 3：economy_source = 1
distinct_target_count > 3：economy_source = 0
```

Trial-level：

```text
economy_score
    = 需要疏散之 sources 的 economy_source 平均
```

如果沒有任何需要疏散的 source，且 decision 沒有產生 action：

```text
economy_score = 1
```

若需要疏散但沒有 action，該錯誤由 Risk Consistency 與 Legality 處理，不得在 Economy 中自行發明額外扣分公式。

## 9.6 Trial-level 輸出

```text
legality_score
priority_score
economy_score
action_consistency_before_gate
fatal_legality_gate
action_consistency
priority_evidence
economy_evidence
```

## 9.7 現行 ideal-action overlap

現行 candidate 與 ideal action 的 flow overlap 不得再命名為 `action_consistency`。

若系統仍需要保留，必須改名為：

```text
action_agreement_with_ideal
```

此欄位只能作為補充分析，不得代替正式 Action Consistency。

---

# 10. Reliability metrics

## 10.1 Trial-level reliability

正式 reliability 僅由 external validator 決定：

```text
R_trial = valid
```

其中：

```text
valid = 1：validator 全部通過
valid = 0：任一必要規則失敗
```

## 10.2 `R_ideal`

```text
R_ideal
    = average(valid of ideal trials)
```

不得固定寫成 1。

若所有 ideal trials 實際通過，結果自然為 1；但必須由 validator 的 trial-level evidence 算出。

## 10.3 `R_deploy`

```text
R_deploy
    = average(valid of deployment trials)
```

不得再使用：

```text
valid、Risk Consistency、Action Consistency 的加權綜合值
```

作為 `R_deploy`。

## 10.4 `Delta_R`

同一組配對條件下：

```text
Delta_R = R_ideal - R_deploy
```

## 10.5 必須分開報告的指標

正式結果必須分開輸出：

```text
R_ideal
R_deploy
Delta_R
valid_rate
risk_consistency
action_consistency
risk_precision
risk_recall
legality_score
priority_score
economy_score
invalid_output_rate
rule_violation_rate
capacity_violation_rate
topology_violation_rate
source_underflow_rate
flow_conservation_violation_rate
```

不得再將上述指標混合成新的正式 reliability 分數。

---

# 11. 模組責任

## 11.1 M0：設定與實驗契約

M0 必須負責：

```text
framework condition 定義
risk threshold
risk_f_beta
scenario_alpha
scenario_beta
rho
hotspot nodes H
trial count
scenario count
sampling policy
root seed
metric policy version
scenario policy id/version
maximum candidate attempts
M6 decision policy id/version
```

M0 必須驗證必要設定，不得讓下游模組自行補預設值。

## 11.2 M4：Ground-truth scenarios

M4 必須：

- 依正式 scenario parameters 產生 `scenario_gt`。
- 確認容量與總人口約束。
- 發布 scenario checksum 與生成參數。
- 不使用 perception residual。
- 先執行 M6 feasibility preflight，只將可完整分配高風險 source request 的
  candidate 納入正式 scenario。
- 發布 `scenario_generation_diagnostics.json` 與
  `scenario_feasibility_report.json`；拒絕的 candidate 不納入正式統計。

## 11.3 M5：Controlled empirical residual propagation

M5 必須：

- 只對 deployment branch 注入 empirical residual。
- 保留 ideal branch 的 `scenario_gt` 不變。
- 發布 sampled residual 與 observation。
- 保存 pair_id 與 scenario lineage。

## 11.4 M6：Decision interface

M6 必須：

- Ideal branch 使用 `scenario_gt`。
- Deployment branch 使用 `observation`。
- 不得讀取 validator 結果後回頭修改 decision。
- 輸出標準 action schema。
- ideal 與 deployment 必須共用同一個 capacity-aware multi-source planner，
  只替換 branch 可見的 decision population。

## 11.5 M7：External validation 與 Action components

M7 必須：

- 使用 `scenario_gt` 驗證兩個 branch。
- 計算完整 post-state。
- 輸出所有 violation flags 與 reasons。
- 計算 Legality、Priority、Economy。
- 套用 fatal legality gate。
- 產生 `recommended_sources`。

## 11.6 M8：Metrics

M8 必須：

- 計算 TP、FP、FN。
- 使用 run config 的 `risk_f_beta` 計算 Risk Consistency。
- 聚合 Action Consistency components。
- 計算 `R_ideal`、`R_deploy`、`Delta_R`。
- 依正式 grouping keys 聚合。
- 不得改寫 M7 的 trial-level validator facts。

## 11.7 M9：Report

M9 只能讀取 M8 canonical metrics，不得重新計算公式。

M9 必須顯示：

```text
metric_policy_version
risk_f_beta
scenario generation parameters
trial count
R_ideal
R_deploy
Delta_R
Risk Consistency breakdown
Action Consistency breakdown
violation rates
```

---

# 12. Aggregation 規則

正式 grouping keys：

```text
topology_id
model_id
ground_truth_regime
framework_condition
decision_interface
metric_policy_version
risk_f_beta
```

每個 rate：

```text
rate = sum(trial flag) / executed_trial_count
```

每個 continuous score：

```text
score = average(trial-level score)
```

GAI `unavailable` rows：

```text
executed_trial_count = 0
metrics = blank/null
availability = unavailable
```

不得填入 0。

---

# 13. API 與資料結構修改

## 13.1 Run config

至少新增：

```json
{
  "risk_f_beta": 2.0,
  "metric_policy_id": "safety_consistency",
  "metric_policy_version": "2.0.0",
  "scenario_alpha": null,
  "scenario_beta": null,
  "rho": null,
  "hotspot_nodes": []
}
```

`scenario_alpha`、`scenario_beta`、`rho`、`hotspot_nodes` 必須由正式 experiment profile 提供，不得使用 `null` 進入執行階段。

## 13.2 Trial metric artifact

```json
{
  "trial_id": "TRIAL-001",
  "pair_id": "PAIR-001",
  "trial_type": "deployment",
  "scenario_id": "SCENARIO-001",
  "valid": 1,
  "expected_high_sources": ["Z1", "Z3"],
  "recommended_sources": ["Z1", "Z2", "Z3"],
  "risk_tp": 2,
  "risk_fp": 1,
  "risk_fn": 0,
  "risk_precision": 0.666667,
  "risk_recall": 1.0,
  "risk_f_beta": 2.0,
  "risk_consistency": 0.909091,
  "legality_score": 1.0,
  "priority_score": 0.8,
  "economy_score": 1.0,
  "action_consistency_before_gate": 0.93,
  "fatal_legality_gate": false,
  "action_consistency": 0.93,
  "action_agreement_with_ideal": 0.72,
  "violation_reasons": [],
  "metric_policy_version": "2.0.0"
}
```

上例只代表 schema 形狀，不得把示例數值寫死到正式實驗。

---

# 14. UI 修改需求

## 14.1 Experiment Settings

新增 Risk Consistency 區塊：

```text
Risk Consistency Formula：F-beta
Beta：可編輯數值欄位，預設 2.0
限制：beta > 0
```

UI 必須將 `risk_f_beta` 視為正式 experiment parameter。

## 14.2 Preflight

執行前顯示：

```text
risk_f_beta
risk threshold
scenario_alpha
scenario_beta
rho
hotspot nodes
trial count
scenario count
metric policy version
```

缺少必要參數時，不得開始 run。

## 14.3 Result Matrix

矩陣可切換：

```text
R_deploy
Delta_R
Risk Consistency
Action Consistency
Valid Rate
Rule Violation Rate
```

每格必須對應相同 grouping keys，不得將不同 beta 的 run 混在同一格。

## 14.4 Paper View

至少顯示：

```text
Regime
Framework Condition
Risk Consistency
Action Consistency
Invalid Output
Rule Violation
R_ideal
R_deploy
Delta_R
```

## 14.5 Metric detail drawer

Risk Consistency 顯示：

```text
beta
TP
FP
FN
Precision
Recall
F-beta
```

Action Consistency 顯示：

```text
Legality
Priority
Economy
Fatal legality gate
Final Action Consistency
```

Reliability 顯示：

```text
ideal valid trials / ideal executed trials
deployment valid trials / deployment executed trials
R_ideal
R_deploy
Delta_R
```

---

# 15. 測試與驗收

## 15.1 Risk F-beta 單元測試

### Case A：漏報

```text
TP = 5
FP = 0
FN = 5
Precision = 1.0
Recall = 0.5
beta = 2.0
Risk Consistency = 0.555556
```

### Case B：無漏報但有誤報

```text
TP = 10
FP = 5
FN = 0
Precision = 0.666667
Recall = 1.0
beta = 2.0
Risk Consistency = 0.909091
```

必須驗證：

```text
Case B > Case A
```

### Beta 可變動測試

同一組 TP、FP、FN，分別使用：

```text
risk_f_beta = 0.5
risk_f_beta = 1.0
risk_f_beta = 2.0
```

必須產生不同結果，且 artifact 與 UI 顯示實際 beta。

## 15.2 Action Consistency 測試

### 完全合法、優先、簡潔

```text
legality = 1
priority = 1
economy = 1
action_consistency = 1
```

### 非法決策

即使：

```text
priority = 1
economy = 1
```

只要：

```text
legality = 0
```

必須：

```text
fatal_legality_gate = true
action_consistency = 0
```

### Economy 超標

```text
distinct target count = 4
economy = 0
```

不得自行套用其他漸進式扣分公式。

## 15.3 Reliability 測試

若 30 個 deployment trials 中 21 個 valid：

```text
R_deploy = 21 / 30 = 0.7
```

不得受 Risk Consistency 或 Action Consistency 數值影響。

若 30 個 ideal trials 全部 valid：

```text
R_ideal = 1.0
```

若只有 27 個 valid：

```text
R_ideal = 0.9
```

不得固定為 1。

## 15.4 Condition isolation 測試

`w/o Two-stage framework` 必須確認：

```text
decision input checksum = scenario_gt checksum
沒有 sampled residual lineage
```

`w/ Two-stage framework` 必須確認：

```text
decision input checksum = observation checksum
存在 residual pool 與 sampled residual lineage
validator ground truth checksum = scenario_gt checksum
```

## 15.5 Reproducibility

相同：

```text
root_seed
run config
input checksum
```

重跑必須得到相同：

```text
scenario checksum
residual sampling checksum
trial metrics
aggregated metrics
```

---

# 16. 舊結果與新版結果隔離

既有舊結果不得直接覆寫。

舊 run 必須保留原本的 metric policy metadata；若舊 run 沒有 metadata，標記為：

```text
legacy_unversioned
```

新版結果必須使用新的：

```text
metric_policy_id
metric_policy_version
risk_f_beta
```

UI 與報表不得在沒有警告的情況下直接比較不同 metric policy version 或不同 `risk_f_beta` 的結果。

正式發表用結果必須來自同一套 approved metric policy。

---

# 17. 完成定義

本次修改完成必須同時滿足：

- [ ] `w/o Two-stage framework` 使用 `scenario_gt` 做 decision input。
- [ ] `w/ Two-stage framework` 使用注入 empirical residual 後的 observation。
- [ ] Ideal 與 deployment trials 有相同 `pair_id` 與 scenario lineage。
- [ ] Validator 一律使用 `scenario_gt`。
- [ ] Capacity 使用完整 post-state 計算。
- [ ] Risk Consistency 使用可配置 F-beta。
- [ ] `risk_f_beta` 可於 UI 設定，預設 2.0，且必須大於 0。
- [ ] Beta 寫入 run config、artifact、表格與 UI。
- [ ] Action Consistency 使用 0.50 Legality、0.35 Priority、0.15 Economy。
- [ ] Legality failure 啟動 fatal gate，Action Consistency 直接為 0。
- [ ] Economy 使用每個 source 最多 3 個 distinct targets 的規則。
- [ ] 現行 ideal-flow overlap 改名為 `action_agreement_with_ideal`。
- [ ] `R_ideal` 由 ideal validation 實際計算。
- [ ] `R_deploy` 等於 deployment valid rate。
- [ ] `Delta_R = R_ideal - R_deploy`。
- [ ] 不再使用 valid、Risk、Action 的加權 composite 作為 `R_deploy`。
- [ ] Scenario generation 使用正式的 `D_total`、`H`、`rho`、`scenario_alpha`、`scenario_beta`。
- [ ] M4 candidate generation 使用 feasibility-constrained deterministic resampling，最多 512 次，拒絕候選不進入正式 trial。
- [ ] M6 使用 `capacity_aware_multi_source_rule_based v1.0.0`，並追蹤 shared target remaining capacity。
- [ ] M7 ideal invariant 通過後才發布正式 M8/M9。
- [ ] 所有正式數值可追溯到 trial-level artifacts。
- [ ] 舊、新 metric policy results 完全隔離。
- [ ] 所有單元測試、整合測試與重現性測試通過。

---

# 18. Codex 執行要求

請依序執行：

1. 先盤點目前 M0、M4、M5、M6、M7、M8、M9、API、UI 的實作位置與既有 schema。
2. 建立修改前差異清單，逐項對照本文件要求。
3. 先更新 schema、DTO 與 config validation，再修改計算流程。
4. 修改 trial-level artifacts 後，再修改 aggregation。
5. 最後修改 M9、API response 與 UI。
6. 不得只改欄位名稱而不改底層計算。
7. 不得用 migration script 偽造缺少的 trial-level evidence。
8. 若既有 artifact 缺少重新計算所需資料，必須從最早必要模組重新執行。
9. 完成後輸出：

```text
修改檔案清單
每項需求的實作位置
公式與資料流說明
API request/response 範例
UI 畫面與欄位說明
測試清單與測試結果
尚未完成或無法確認的事項
```

10. 若發現本文件未定義的科學決策，不得自行選擇；必須停止該部分實作並列為待確認事項。

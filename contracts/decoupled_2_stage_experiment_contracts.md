# Decoupled 2-Stage Experiment Contracts

本契約對應 `decoupled_2_stage_experiment_v1` 的正式計算政策 `safety_consistency/2.0.0`。M9 產生的是 3 個 topology × 5 個 perception model 的 15 份表格；每一份表格中的 ideal 與 deployment 皆為可追溯的配對結果。

## M0 Run Manifest

`M0/experiment_manifest.json` 凍結所有會影響結果的設定：

```text
root_seed, split, trial_count_per_condition, scenarios_per_regime
risk_threshold, risk_f_beta
scenario_alpha, scenario_beta, rho, hotspot_selection
metric_policy_id, metric_policy_version
scenario_policy_id, scenario_policy_version, max_candidate_attempts
decision_policy_id, decision_policy_version
input_checksums, framework_conditions, decision_interfaces
```

已建立的 run 不會在原地改寫設定；更改 beta 或其他參數必須建立新 run。

## M1/M2 Perception Contract

`M1/perception_results.parquet` 的 canonical error 是：

```text
error = predicted_count - ground_truth_count
```

M2 僅從 M1 讀取，正式 residual pool key 是：

```text
model_id + ground_truth_regime
```

每個 pool 保留完整 empirical residual samples。Detection 與 Density 由 model/paradigm 與 dataset 對應分離，不能混用 GT 或 residual。

## M4 Scenario GT Contract

`M4/scenario_gt.jsonl` 每列必含：

```text
scenario_id, topology_id, ground_truth_regime, D_total
H, rho_requested, rho_actual
scenario_alpha, scenario_beta, scenario_seed
scenario_seed_base, generation_attempt
candidate_rejection_count, candidate_rejection_reason_counts
scenario_gt_population, capacity_check_passed
decision_feasibility_status, decision_feasibility_reasons
scenario_policy_id, scenario_policy_version
m6_decision_policy_version
scenario_checksum, topology_checksum, capacity_checksum
```

場景必須滿足每個 source node 人數非負且不超過 capacity，所有 source node 的加總精確等於 `D_total`。權重由 `Beta(scenario_alpha, scenario_beta)` 生成。

M4 只接受通過 `capacity_aware_multi_source_rule_based v1.0.0` feasibility
preflight 的 candidate。每個 topology × regime 必須取得設定數量的正式
scenario；拒絕的 candidate 不進入 `scenario_gt.jsonl` 或正式 trial，並保留在：

```text
M4/scenario_generation_diagnostics.json
M4/scenario_feasibility_report.json
```

單一 scenario 最多嘗試 512 個 deterministic candidate seeds；超過上限仍
無法取得可行 scenario 時，M4 Failed，Run 不進入 M5/M8/M9。

## Paired Framework Contract

```text
w/o Two-stage framework
  trial_type = ideal
  decision input = scenario_gt_population
  residual / observation / decision buffer = prohibited

w/ Two-stage framework
  trial_type = deployment
  observed_population[node] = max(0, round_half_up(scenario_gt[node] + sampled_residual[node]))
  decision input = observed_population
```

同一 `pair_id` 的 ideal/deployment 必須有相同的 `scenario_id`、`scenario_checksum`、`topology_checksum`、`capacity_checksum`。只有 deployment 有 residual pool、sampling seed 與 observation checksum。

## M5/M6 Contract

`M5/observation_trials.parquet` 對每個 deployment trial 保存：

```text
pair_id, residual_pool_id, residual_pool_count
sampling_policy, sampling_seed, sampled_residuals
observation_population, observation_checksum
```

`M6/decision_actions.parquet` 的 action schema 固定為：

```text
source_id, target_id, move_count, priority_metadata
```

M6 對 ideal 與 deployment 使用相同的 capacity-aware multi-source planner，
只替換自身可見的 `decision_population`：ideal 是 `scenario_gt`，deployment
是 observation。planner 依 source utilization、requested quantity 與 natural
node id 排序，並追蹤非出口 target 的剩餘容量；`priority_metadata` 保存
requested/allocated quantity、selected rank、target remaining capacity 與
allocation order。

Rule-based decision 只可讀取該 branch 的 decision input；不能讀取 `scenario_gt` 來修正 deployment action，也不能使用 validator 結果回頭改 action。

## M7 Validator Contract

M7 必須呼叫概念上的 `V(action, topology, scenario_gt)`，真值一律是 M4 的 `scenario_gt`。每個 trial 的結果含：

```text
invalid_output, topology_violation, unknown_target_violation
forbidden_target_violation, capacity_violation
source_underflow_violation, flow_conservation_violation
rule_violation, valid, violation_reasons, post_population
```

每筆 trial 另保存：

```text
decision_input_mode
decision_input_checksum
validation_truth_source_stage_id = M4
validation_truth_checksum = scenario_checksum
```

所有通過 M4 feasibility preflight 的 ideal scenarios 必須通過 M7。若
ideal invariant 失敗，Run Failed 且不發布正式 M8/M9。

非出口 node 的 post-state 為：

```text
post_population[node] = scenario_gt[node] + incoming[node] - outgoing[node]
```

任一必要驗證失敗即 `valid = 0` 且 `rule_violation = 1`。

## M8 Metric Contract

Risk Consistency 以 `scenario_gt / capacity >= risk_threshold` 取得 expected sources，並以實際 action 取得 recommended sources；它使用 immutable run config 中的 `risk_f_beta`：

```text
F_beta = (1 + beta^2) * precision * recall / (beta^2 * precision + recall)
```

Action Consistency：

```text
before_gate = 0.50 * legality + 0.35 * priority + 0.15 * economy
legality = 0 -> final action_consistency = 0
```

`action_agreement_with_ideal` 是補充欄位，不可替代正式 Action Consistency。

Reliability 不得混入 Risk 或 Action 分數：

```text
R_trial  = valid
R_ideal  = average(valid of ideal trials)
R_deploy = average(valid of deployment trials)
Delta_R  = R_ideal - R_deploy
```

正式 aggregation key：

```text
topology_id, model_id, ground_truth_regime, framework_condition
decision_interface, metric_policy_version, risk_f_beta
```

GAI 尚未提供時，`availability = unavailable`、`executed_trial_count = 0`，所有 metric 必為 `null`，不得填 0。

## M9 Delivery Contract

M9 僅讀 M8 canonical metrics，不重新計算公式。輸出包含每個 topology/model 的 CSV、Markdown，以及全體 CSV、Markdown、XLSX、ZIP、delivery manifest、reproducibility manifest、run summary。

M9 manifest 另保存 scenario policy、M6 decision policy、accepted scenario
count、rejected candidate count，以及 M4 generation diagnostics checksum。

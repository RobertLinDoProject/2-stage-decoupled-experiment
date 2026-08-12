# Ideal Baseline Test Results

## 執行方式

```powershell
$env:PYTHONPATH='backend/src'
python -m unittest discover -s backend/src/two_stage/tests -p 'test_*.py' -v
```

## 測試項目

| 測試 | 目的 | 結果 |
|---|---|---|
| `test_ideal_residual_isolation` | ideal decision 只使用 `scenario_gt` | 通過 |
| `test_cross_model_ideal_consistency` | 五個 model 的 ideal 結果一致 | 通過 |
| `test_repeated_scenario_is_deterministic` | scenario reuse 與 seed 重現性 | 通過 |
| `test_m4_feasibility_constrained_generation_is_deterministic` | 相同 seed 產生相同 accepted scenarios/diagnostics | 通過 |
| `test_m4_candidate_rejections_are_not_formal_scenarios` | rejected candidates 不進正式 scenario artifact | 通過 |
| `test_m4_feasibility_rejects_unallocated_ideal_request` | 不可完整分配的 scenario 被 M4 拒絕 | 通過 |
| `test_m6_coordinates_shared_capacity_and_spills_by_priority` | multi-source shared target capacity coordination | 通過 |
| `test_m7_records_observation_input_but_m4_truth` | deployment observation 與 M4 truth source 隔離 | 通過 |
| `test_feasible_ideal_plan_passes_independent_validator` | feasible ideal plan 通過獨立 M7 validator | 通過 |
| `test_reliability_aggregation_known_four_of_thirty` | M8 reliability 依 trial facts 聚合 | 通過 |
| `test_validator_post_state_detects_multi_source_capacity` | M7 post-state capacity violation | 通過 |
| `test_validator_checks_non_source_target_capacity` | 非 source target capacity 檢查 | 通過 |
| `test_ui_baseline_source_keeps_consistency_guard` | UI baseline 不靜默平均 | 通過 |

執行結果：`Ran 13 tests ... OK`。

## Formal Run Evidence

正式 `root_seed=114、K=30、S=8` Run 的結果：

```text
M4 accepted scenarios = 72
M4 rejected candidates = 12
M7 ideal failures = 0
M8 unique R_ideal = 1.0
```

## 限制

測試使用 deterministic fixture 與正式 Run artifact 做 regression evidence，
不修改 `Data/` 原始輸入，也不將 `R_ideal` 強制設定為 1。

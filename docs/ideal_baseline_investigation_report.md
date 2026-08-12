# Ideal Baseline Investigation Report

## Executive Summary

本次以最新 feasibility-constrained scenario generator 重新執行正式 Run：

- `root_seed=114`
- `split=test`
- `trial_count_per_condition=30`
- `scenarios_per_regime=8`
- `risk_f_beta=2.0`
- scenario policy：`topology_capacity_hotspot_beta_v1/feasibility_constrained_v1`
- M6 policy：`capacity_aware_multi_source_rule_based/1.0.0`

M4 先產生 Beta candidate，再用同一個 M6 planner 做 feasibility preflight；
只有可完整分配高風險 source request 的 candidate 才成為正式 scenario。拒絕
candidate 只保留在 generation diagnostics，不進入正式 scenario、M5 trial 或
`R_ideal`。

## Reproduction Result

- run_id：`decoupled-2-stage-20260803T062030574554Z-0cb0d0e3`
- status：`SUCCEEDED`
- required formal scenarios：72
- accepted formal scenarios：72
- rejected candidates：12
- M7 ideal trials：1350
- M7 ideal failures：0
- M8 ideal metric rows：45（15 configurations × 3 regimes）
- unique `R_ideal` values：`[1.0]`

因此，新的正常成功 Run 之 `R_ideal` 是由 M7 trial facts 聚合得到的 1.0，
不是在 M8 或 UI 硬編碼。Deployment branch 的 `R_deploy` 仍保留正式 residual
造成的實際失敗差異，不應被強制設為 1.0。

## Branch Isolation

- M6 ideal decision input：`scenario_gt`
- M6 deployment decision input：M5 `observation`
- M7 ideal/deployment validation truth：一律為 M4 `scenario_gt`
- M7 不把 observation 當真值。
- M6 不讀 M7，也不使用 scenario_gt 修正 deployment action。
- ideal branch 不使用 residual；deployment branch 才使用 M2 empirical residual。

## Scenario Generation Evidence

正式 artifacts：

```text
M4/scenario_gt.jsonl
M4/scenario_manifest.csv
M4/scenario_generator_policy.json
M4/scenario_generation_diagnostics.json
M4/scenario_feasibility_report.json
M7/ideal_invariant_report.json
```

每個正式 scenario 保存：

```text
scenario_seed_base
scenario_seed
generation_attempt
candidate_rejection_count
candidate_rejection_reason_counts
decision_feasibility_status
scenario_policy_id/version
scenario_checksum
```

generation diagnostics checksum：

```text
c127f2300984f13214a1bce8f3da2151a24d4e0152626f7dd9a17d56ffde81f7
```

## M6 / M7 Invariant

M6 使用 capacity-aware multi-source coordination：

- source 依 utilization、requested move count、natural node id 排序。
- target 依 total cost 與 node id 排序。
- 非出口 target 追蹤 shared remaining capacity。
- 同一 source 必要時可分配到多個 target。
- `priority_metadata` 保存 requested/allocated quantity、selected rank、
  target remaining capacity 與 allocation order。

M4 accepted scenario 全部通過 M6 feasibility；M7 `ideal_invariant_report.json`
顯示 `ideal_failure_count=0`。若未來 ideal invariant 失敗，Run 會 Failed，並
不發布正式 M8/M9。

## M8 Aggregation

M8 只從 M7 trial facts 聚合：

```text
R_ideal  = average(valid of ideal trials)
R_deploy = average(valid of deployment trials)
Delta_R  = R_ideal - R_deploy
```

不把 Risk Consistency 或 Action Consistency 混入 Reliability composite，也不
把 unavailable 轉成 0。

## UI Mapping

UI 的 Ideal Baseline 以 topology × regime 顯示 `w/o + rule_based + ideal`。
五個 model rows 只做 `1e-6` 一致性檢查，不平均；不一致時顯示
`Ideal baseline consistency error`。

Deployment matrix 固定顯示 `w/ + rule_based`，Paper View 才顯示完整的
Framework、Decision Interface、Regime rows 與篩選結果。失敗 Run 會清除舊成功
Run 的畫面結果，並提供 M4/M7 diagnostics 下載。

## Conclusion

目前文件與程式一致的解讀是：`R_ideal=1.0` 代表在已通過 M6 feasibility
preflight 的 scenario set 上，Rule-based planner 經 M7 獨立驗證全部通過；它
不是 Perception accuracy，也不代表任意人口分布都可完成決策。Perception
residual 對部署可靠度的影響應由 `R_deploy` 與 `Delta_R` 觀察。

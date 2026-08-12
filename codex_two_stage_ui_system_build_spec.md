# Decoupled 2-Stage Experiment 系統建置規格

## 0. 本版結論

本系統目前的正式建置目標是：

```text
使用 Data/ 內正式 A/B Data，
一鍵執行 decoupled 2-stage experiment，
產生論文決策介面效能比較所需的 15 組表格。
```

系統正式名稱固定為 Decoupled 2-Stage Experiment；Table 3-style tables 只是 M9 的論文輸出格式。

## 1. 論文對齊目標

論文核心問題是比較：

```text
perception 在真實部署誤差下，是否會造成 decision interface performance 的可觀差異。
```

因此本版只保留完成這個問題必要的流程：

```text
M0 inputs/profile
M1 perception canonicalization
M2 empirical residual pools
M3 topology/capacity canonicalization
M4 scenario_gt generation
M5 controlled empirical residual propagation
M6 observation artifact
M7 decision validation
M8 metrics aggregation
M9 delivery tables/manifests
```

## 2. 正式 Profile

```text
profile_id: decoupled_2_stage_experiment_v1
run_purpose: development | exploratory | formal
input_root: Data/
output_root: storage/published/runs/<run_id>/
```

`formal` Run 才是論文正式輸出；`development` 與 `exploratory` 用於小範圍流程驗證、provider smoke 與成本/失敗率觀察。每個 Run 的 snapshot 會凍結以下選擇，不要求每次都執行全量條件：

```text
selected_rule_sources
selected_interfaces
selected_topology_ids
selected_model_ids
selected_regimes
trial_count_per_condition
run_purpose
```

完整人工 Rule-based 論文範圍是 `3 topologies x 5 perception models x 3 regimes`；小型 Run 可透過上述 selection filters 限定子集合。每個 `formal` Run 必須使用至少 30 trials，且所有選定且必要的結果完成後才可發布 M8/M9。Ideal branch 的 action scope 是 `rule source x topology x regime x scenario/trial`，不含 perception model；model-specific residual 與 observation 只進入 deployment branch。

## 3. 正式輸入資料

### Topology

```text
Data/Topology資料/
```

必要 topology：

```text
fcu
Taichung Lantern Festival
Taipei New Year's Eve
```

每個 topology 必須有：

```text
<name>_map_neww.json
<name>_neighbors.json
<name>_rule.json
```

Topology input contract 固定採用：

```text
graph_directionality = undirected
adjacency_semantics = symmetric
edge_cost_directionality = directed
```

`A → B` 與 `B → A` 必須同時存在，代表兩個區域相鄰；兩個方向的 edge cost 可以不同，代表不同疏散方向的難度、時間或風險。M3 必須保留兩筆方向性 edge，不得將雙向 cost 合併。

### Perception

```text
Data/Perception資料/
```

必要檔案：

```text
A1_benchmark_samples_combined.csv
A2_perception_model_registry.csv
A3_model_predictions_raw.csv
```

A2 必須提供 5 個 paper-eligible perception models。Density 與 Detection residual pool 必須分離，不得合併。

## 4. 固定實驗政策

本版先固定政策，不讓 UI 把研究選項變成阻礙執行的決策點。

```text
residual_pool_scope = all five models, separated by model/regime/paradigm
sampling = with_replacement
trial_count_default = 30
exploratory_trial_count = 5 to 10
scenarios_per_regime_default = 8
scenario_policy_id = topology_capacity_hotspot_beta_v1
scenario_policy_version = feasibility_constrained_v1
max_candidate_attempts_per_scenario = 512
negative handling = floor_at_zero
rounding = round_half_up
capacity handling = M6 coordinates visible non-exit target capacity; M7 independently validates scenario_gt post-state
count-to-node mapping = regime-matched empirical residual per source node
minimum residual pool = fail if empty
M6 decision policy = capacity_aware_multi_source_rule_based v1.0.0
R_ideal = average(ideal M7 valid); R_deploy = average(deployment M7 valid); Delta_R = R_ideal - R_deploy
GAI = optional decision interface; unavailable/failed is never converted to 0
```

### 4.1 分階段執行控制

系統目前支援以下執行順序：

```text
Preflight (0 external GAI calls)
-> Rule-based exploratory pilot
-> Rule-based formal run (30 trials or more)
```

目前可用的本地 GAI 執行模式為 `live` + `ollama`；未完成本地 provider preflight 時仍會安全回報 unavailable。因此：

```text
preflight_never_calls_provider = true
formal_gai_calls_are_budget_limited = true
```

Rule-based 永遠不產生 GAI call。選取 `gai` 時，系統使用本機 Ollama 的 `mistral:7b-instruct-v0.3-q4_K_M`，每次只要求一筆 canonical action。provider、timeout 或預算不足且沒有 terminal decision 時才是 unavailable；模型已回應但 `invalid_output` 或 `decision_infeasible` 時，建立 `valid=0` terminal trial fact 並納入 M8，不使用 Rule-based fallback。

GAI 正式執行為背景 Run，Run Detail 會顯示目前 stage、action episode、已完成／預估呼叫數、invalid output 與 transport failure。M8/M9 只在必要的 ideal/deployment episodes 完成後 atomic publication。

## 5. M0-M9 契約

### M0 Input / Preflight

輸出：

```text
M0/experiment_manifest.json
M0/preflight_report.json
```

必須檢查：

```text
3 topology files complete
5 paper-eligible perception models
15 condition matrix
input checksums
GAI unavailable is not failure
```

### M1 Perception Canonicalization

輸出：

```text
M1/perception_results.parquet
M1/perception_results.csv
M1/perception_results_manifest.json
M1/m1_quality_report.json
M1/excluded_samples.csv
```

必要欄位：

```text
sample_id
dataset_id
split
paradigm
model_id
model_version
ground_truth_count
predicted_count
error
absolute_error
ground_truth_regime
predicted_regime
source_ref
```

`error = predicted_count - ground_truth_count`。正值是高估，負值是低估。

### M2 Error Distribution

輸出：

```text
M2/error_samples.parquet
M2/error_samples.csv
M2/regime_statistics.parquet
M2/regime_statistics.csv
M2/error_distribution_summary.json
M2/m2_error_model.json
M2/m2_quality_report.md
```

M2 只能讀 M1 canonical artifact 的語意結果，不直接把 Density / Detection residual 混用。

### M3 Topology Canonicalization

輸出：

```text
M3/<topology_id>/topology_spec.json
M3/<topology_id>/topology_nodes.csv
M3/<topology_id>/topology_edges.csv
M3/<topology_id>/topology_rules.json
M3/<topology_id>/validation_report.json
M3/topology_manifest.json
```

必須 materialize：

```text
nodes
edges
capacity
external exits
source eligible nodes
rules
```

### M4 Scenario GT

輸出：

```text
M4/scenario_gt.jsonl
M4/scenario_manifest.csv
M4/scenario_generator_policy.json
M4/scenario_generation_diagnostics.json
M4/scenario_feasibility_report.json
```

保證：

```text
same root_seed reproducible
capacity respected
total_population exact
only M6-feasible candidates become formal scenarios
rejected candidates are diagnostics, not formal scenarios
each topology × regime has the configured number of accepted scenarios
```

每個正式 scenario 另保存 `scenario_seed_base`、`scenario_seed`、
`generation_attempt`、`candidate_rejection_count`、
`candidate_rejection_reason_counts`、`decision_feasibility_status`、
`scenario_policy_id` 與 `scenario_policy_version`。若單一 scenario 在
512 次 deterministic candidate attempts 內仍無法通過 M6 feasibility，M4
直接 Failed，不產生正式 M8/M9 結果。

### M5 Controlled Empirical Residual Propagation

M5 使用 M2 empirical residual pool，將 perception residual 映射到 M4 scenario_gt 的 topology source nodes，產生 deployment observation。

不得命名為 fake error injection，也不得用 synthetic noise 冒充正式 perception residual。

輸出：

```text
M5/observation_trials.parquet
M5/observation_trials.csv
M5/controlled_residual_policy.json
M5/ideal_branch_lineage.json
```

每個 deployment trial 保存 residual pool、sampling seed、sampled residual、
observation population 與 checksum；ideal branch 不產生 observation。

### M6 Decision Artifact

輸出：

```text
M6/action_trials.parquet
M6/decision_actions.parquet
M6/m6_manifest.json
```

M6 使用自身可見的 `decision_population` 產生 capacity-aware、multi-source coordinated action。ideal branch 使用 `scenario_gt`；deployment branch 使用 M5 observation；兩者使用相同 M6 planner。

M6 不讀 M7，也不使用 `scenario_gt` 修正 deployment decision。非出口 target 的 shared remaining capacity 必須被追蹤，避免多個 source 同時將 target 超額填入。

### M7 Decision Validation

M7 validator 必須使用 M4 `scenario_gt` 當真值，不得從 M6 observation 取真值。

M4 會先執行 M6 decision feasibility preflight。候選 scenario 只有在能完成高風險 source 合法分配時，才會成為正式 scenario；不可行候選以 deterministic candidate seed 重新抽樣，並記錄於 `M4/scenario_generation_diagnostics.json`。每個 topology × regime 仍必須產生固定數量的正式 scenarios，預設最多嘗試 512 次；超過上限仍無法產生可行 scenario 時，M4 失敗、不進入 M5，也不將候選納入 ideal metric。

Stage II ideal baseline 只代表符合目前 topology、capacity 與 M6 decision feasibility 的 scenario set，不代表所有任意人口分布都能完成目前的單步決策規則。

必要檢查：

```text
capacity
edge
direction
source_underflow
flow_conservation
violation_reason
```

輸出至少包含：

```text
M7/decision_validation_trials.parquet
M7/decision_validation_trials.csv
M7/validator_manifest.json
M7/ideal_invariant_report.json
```

### M8 Metrics

輸出：

```text
M8/decoupled_2_stage_metrics.parquet
M8/decoupled_2_stage_metrics.csv
M8/metrics_manifest.json
```

必要 metrics：

```text
valid_rate
invalid_output_rate
rule_violation_rate
capacity_violation_rate
topology_violation_rate
action_consistency
risk_consistency
r_ideal
r_deploy
delta_r
```

GAI 未設定時為 `unavailable`，不得填 0。

### M9 Delivery

輸出：

```text
M9/decoupled_2_stage_<topology_id>__<model_id>.csv
M9/decoupled_2_stage_<topology_id>__<model_id>.md
M9/decoupled_2_stage_all_tables.csv
M9/decoupled_2_stage_all_tables.md
M9/decoupled_2_stage_all_tables.xlsx
M9/decoupled_2_stage_all_tables.zip
M9/delivery_manifest.json
M9/reproducibility_manifest.json
M9/run_summary.json
```

M9 delivery 與 reproducibility manifest 也保存 scenario policy、accepted
scenario count、rejected candidate count，以及
`M4/scenario_generation_diagnostics.json` checksum。

## 6. UI 規格

UI 參考 `w_two_stage_ui_design.md`，但本版只做低資訊量的一鍵實驗介面。

第一屏只顯示：

```text
Decoupled 2-Stage Experiment
root seed
trials / condition
scenarios / regime
Risk Consistency β
執行實驗
```

執行後只顯示：

```text
5 models x 3 topologies matrix
regime filter
metric filter
selected configuration Paper View
download CSV / Markdown / XLSX / ZIP
```

若 Run 在 M4 或 M7 失敗，API 仍回傳 Failed Run summary 與 `run_id`、
`stage_id`、failure message、failure details。UI 會清除前一個成功 Run 的
結果，顯示 failure diagnostics 下載入口，不會繼續顯示舊的 Ideal Baseline。

Framework Condition 與 Decision Interface 必須分開顯示：

```text
Framework Condition = w/o Two-stage framework | w/ Two-stage framework
Decision Interface = rule_based | gai_reserved
```

GAI unavailable 不得顯示為 0。

## 7. 移除範圍

本版主流程不使用：

```text
PostgreSQL
Redis
Celery
Alembic migration
DB-backed workflow
舊 UI-0～UI-5 多頁 workflow
非必要圖表
歷史測試 fixture
```

保留：

```text
Data/
M0-M9 核心資料流
Local artifact publication
FastAPI
React/TypeScript
Docker Compose api/frontend
GAI adapter interface
```

## 8. 人工／AI 規則來源比較 profile

在人工-only profile 外，系統支援 `decoupled_2_stage_rule_source_comparison_v1`，沿用同一套 M0～M9 orchestration 比較：

```text
rule source (human_manual_v1 / ai_generated_derived_v1)
× M6 interface (rule_based / gai)
× framework (w/o / w/)
```

每個 topology × perception model × regime 產生八列原始 M8 rows。`w/o` 使用 `scenario_gt`，`w/` 使用同一組配對 trial 的 residual-derived `observed_population`。人工與 AI rule source 共用 M4 scenario cohort、M5 residual sample 與 observation checksum；若 node set 或 capacity checksum 不一致，M3 comparison preflight 直接失敗，不進行不公平的 paired comparison。

M6 使用 selected rule source 的 topology/rules，但 M7 永遠使用 `human_manual_v1` topology/rules 與 M4 `scenario_gt` 作為共同 gold-standard。M7 不得讀 observation 作為真值，也不得讓 AI rule source 自我驗證。每筆 M7 record 必須保留 decision rule source、decision topology checksum、validation rule source、validation topology checksum、decision interface 與 M4 truth checksum。

AI 原始 map/neighbors 不直接覆蓋人工 package。`scripts/materialize_ai_topology.py` 產生獨立的 `Data/Topology資料/AI生成` package 與 `*_rule.json`，記錄 source checksum、AI-derived graph fields、system-governed contract fields、出口與 cost semantics。AI package 通過與人工相同的基本 input contract 後才可進入 comparison M3。

M4 comparison cohort 先以共同 human topology／capacity contract 執行介面無關的 deterministic feasibility oracle；不呼叫 GAI，也不依 GAI 成功與否重抽 scenario。Rule-based 與 GAI 共用同一批 M4 scenario、M5 residual／observation、M7 gold-standard 與 M8 aggregation。

目前 GAI execution mode 可設定為 `live` + `ollama`，使用 `mistral:7b-instruct-v0.3-q4_K_M` 產生逐步 canonical M6 action；未通過 provider preflight、budget 或 transport 沒有 terminal decision 時回報 `unavailable`。若模型已回應但為 `invalid_output` 或 `decision_infeasible`，GAI row 保留完整 lineage，建立 `valid=0` terminal trial fact 並納入 M8；不使用 Rule-based 結果替代。

Paper View 在選定 topology × model × regime 後，提供 Rule Source、M6 Decision Interface、Framework、Regime 四個獨立篩選器。下方表格顯示八列 branch-specific `Branch Valid Rate` 與 violation/consistency；上方四列 paired reliability summary 分別顯示人工/Rule-based、人工/GAI、AI/Rule-based、AI/GAI 的 `R_ideal`、`R_deploy`、`Delta_R`。`ALL` 只顯示 M8 原始 aggregate rows，不平均、不重算，也不把 `unavailable` 轉成 0。

## 9. Perception Error Boundary 與敏感度分析

系統提供唯讀的 `Perception Error Boundary` 分析，條件為 `Topology × Model × Regime × Rule Source × 決策方式`。此分析是 counterfactual sensitivity analysis，不修改 M5、M7、M8、M9 artifact，也不取代正式論文結果。

Analytical boundary 使用 M6 現有 risk rule：

```text
first_high_risk_count = ceil(risk_threshold × capacity)
last_non_high_count = first_high_risk_count - 1
signed_error_boundary = last_non_high_count - ground_truth_population
```

Existing Run Analysis 只讀取正式 M5 保存的 residual、scenario_gt、observation 與 M7 facts，快速顯示 `Observed Estimate`。使用者明確啟動 Boundary Sweep 後，才計算 `Computed Boundary`，其 residual scale 定義為：

```text
alpha = 0  → ground-truth observation
alpha = 1  → formal M5 observation
observed_alpha = max(0, round_half_up(scenario_gt + sampled_residual × alpha))
```

v1 固定使用 `lambda=0.00～1.00、step=0.05`，重播相同 M6 planner，再以 M4 scenario_gt、人工 topology/rules 的 M7 validator 驗證。它可回報 `R_deploy > 0`、`>=0.50`、`>=0.80`、`>=0.95` 所需的 safe critical lambda 和 residual magnitude reduction。若粗略曲線首次觀察到 `R_deploy=0`，另建立 `lambda=0.00` 到第一個零點、step `0.01` 的 focus curve；完整粗略曲線仍保留，這些結果不修改正式 M8/M9。

這些數值只能解讀為特定 topology、model、regime、rule source 與決策方式下的條件式容忍範圍；若 `R_ideal = 0`，則顯示 baseline failed，不能解讀為 perception error 的可恢復邊界。GAI 沒有 replayable action 時只顯示 analytical boundary，不重呼叫模型、不用 Rule-based action 代替。

分析 endpoint：

```text
GET /api/v1/decoupled-2-stage-experiment/runs/{run_id}/perception-error-boundary
  ?rule_source_id=...
  &topology_id=...
  &model_id=...
  &regime=...
  &decision_interface=...
```

回應與下載均明確標示 `COUNTERFACTUAL ANALYSIS — NOT FORMAL M8/M9 RESULT`；服務只讀 published artifact 並保留 M5/M7 checksum lineage。

### GAI adapter 與本地 Ollama 執行政策

系統保留 Gemini HTTP adapter，同時新增 `OllamaActionDecisionAdapter` 與 `DecisionInterfacePort`。Ollama request 只含當下 M6 action step：source、branch-visible population、合法 candidates、target `max_count`、成本與 checksum；不含 M4 truth label、M7 result 或 M8 metric。模型只能直接回傳一筆 `{action_id, from_node, to_node, count}`，不提供 plans 讓模型選擇。

每個 action step 都執行嚴格 contract validation；count 必須等於選定 target 的 max_count。錯誤不修補、不換 target、不 fallback。成功 action 才更新 shared remaining capacity，完整 episode 後由人工 M7 gold-standard 驗證。Trace 保存 model、prompt、step/context/request/response checksum、latency、token count、retry、contract status 與錯誤分類。

GAI Run 以背景方式執行，`POST /runs` 回傳 `202 + run_id`，`GET /runs/{run_id}` 回傳目前 stage；可使用 `/cancel` 與 `/resume`。M8/M9 只在 ideal/deployment episodes 完成後發布。GAI `available`、`invalid_output`、`decision_infeasible` 與 `unavailable` 分開顯示；前兩者是已執行的模型結果，計入 denominator，即使 reliability 為 0 仍保留數值；只有 unavailable 才維持 null。

### OpenAI M6 provider 與額度耗盡

M6 另支援由 Run Settings 選擇的 OpenAI Responses API provider，預設模型為 `gpt-5-nano-2025-08-07`。OpenAI 使用與 Ollama 相同的單步 canonical action contract 與 M6 本地 contract validation；Structured Outputs 只降低格式錯誤，不取代 M7 human gold-standard validator。`OPENAI_API_KEY` 只注入 API／Worker container，絕不進入 frontend、Run snapshot、trace、artifact 或錯誤訊息。

OpenAI account quota 與本地 GAI budget 是不同概念。若 OpenAI 回傳 `insufficient_quota`、billing hard limit 或等價的額度耗盡錯誤，Run 立即停止後續呼叫，但保留已完成 M6 action、M7 validation、journal 與 trace，發布 `PARTIAL_QUOTA_EXHAUSTED` 的 M8/M9 partial result。只有已完成同一 topology × model × regime × rule source × decision interface 的 ideal/deployment pair 才進入 R_ideal、R_deploy、Delta_R；尚未完成的一側不填 0，也不計入 denominator。額度恢復後可使用同一 Run ID Resume，已完成 action step 不重跑。模型輸出 `invalid_output` 或 `decision_infeasible` 則仍是正式 valid=0，不可與 quota exhausted 混為一談。

## 10. Perception Error Tolerance Boundary v1

系統的感知誤差分析採兩層設計：

- **Existing Run Analysis**：開啟 Selected Configuration Detail 時預設顯示，直接讀取 M4、M5、M7 已發布 trial lineage，快速產生 `Observed Estimate`。不重新執行 M5～M8。
- **Boundary Sweep Run**：只有使用者明確啟動才執行，固定以 `lambda=0.00, 0.05, ..., 1.00` 重用相同 scenario、residual、seed、topology 與 capacity，重跑 Rule-based M5→M6→M7。

```text
observed_population(lambda)
  = max(0, round_half_up(scenario_gt + lambda × sampled_residual))
```

`lambda=0` 等於 ideal ground-truth input，`lambda=1` 等於正式 M5 observation。分析必須沿用正式 `round_half_up` 與 `floor_at_zero`，不得重抽 residual、合成 synthetic noise 或修改 M2/M4/M6/M7 科學定義。v1 不支援 lambda 大於 1、zone-level boundary、bootstrap CI 或 adaptive refinement。

每個 lambda 保存 valid/executed、`R_deploy`、MAE、RMSE、STD、P90、最大絕對誤差、低估／高估比例與 M7 violation evidence。固定檢查 `R_deploy > 0`、`>=0.50`、`>=0.80`、`>=0.95`。`safe_critical_lambda` 採保守定義：從 lambda=0 開始，直到該點以前每個已測試點都達到目標。所需降低的是 residual magnitude reduction，不是 model accuracy improvement。

狀態規則：

- `REACHED`：測試範圍內找到安全邊界。
- `ABOVE_SEARCH_RANGE`：lambda=1 仍達到目標。
- `NOT_REACHED`：目前 lambda 範圍沒有找到安全邊界。
- `BASELINE_FAILED`：`R_ideal=0`，不可把改善直接歸因於 perception error。
- `NON_MONOTONIC_WARNING`：曲線有上升段，保留結果但以 safe boundary 為主要判讀值。

Boundary Job 沿用現有 file-backed serial background executor，不新增資料庫 migration 或另一套 M0～M9 runner。獨立結果保存於：

```text
published/runs/{source_run_id}/boundary_analysis/{boundary_job_id}/
```

並產生 `boundary_config.json`、`source_lineage.json`、`lambda_curve.csv`、`boundary_targets.json`、`trial_results.jsonl`、`monotonicity_audit.json`、`boundary_summary.json` 與 `boundary_report.md`。來源 Run 的 M0～M9、M8 metrics 與 M9 report 不修改。所有下載均標示：

```text
COUNTERFACTUAL ANALYSIS — NOT FORMAL M8/M9 RESULT
```

GAI 既有結果可做 Existing Run Analysis；Boundary Sweep v1 不重呼叫 Ollama，UI 必須顯示 `Computed Boundary unavailable — GAI replay is not enabled.`。

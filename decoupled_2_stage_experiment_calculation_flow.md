# Decoupled 2-Stage Experiment：M0～M9 實驗資料建置與計算流程

## 結論

這個系統把正式 Perception 資料中的「模型實際高估／低估人數」帶入三個正式 topology，並且對每一個情境同時產生一個不受感知誤差影響的理想決策，以及一個受該模型 empirical residual 影響的部署決策。兩者使用同一份 ground-truth scenario、同一份 topology 與同一份 capacity，因此最後的差異可以歸因於 Perception 誤差進入決策輸入後造成的影響。

一次 run 的範圍是：

```text
3 topologies × 5 perception models = 15 configurations
每個 configuration × LOW / MEDIUM / HIGH
每個 regime × K 個 paired trials
每個 pair = 1 ideal trial + 1 deployment trial
```

正式 Rule-based 結果使用 `safety_consistency/2.0.0`。目前 M6 GAI 執行介面可使用本機 Ollama 的 `mistral:7b-instruct-v0.3-q4_K_M`；provider、budget 或 transport 沒有 terminal decision 時回報 `unavailable`。GAI adapter 與 Paper View 保留列仍存在；已執行的 `invalid_output`／`decision_infeasible` 會以 `valid=0` 納入 M8，但不會混入 Rule-based 結果。

---

## 1. 核心資料流

```text
正式 A/B Data
  │
  ├─ A1 sample 真值 + A2 模型名冊 + A3 模型預測
  │      └─ M1 canonical perception results
  │              └─ M2 empirical residual pools
  │
  └─ Topology map + neighbors + rules
         └─ M3 canonical topology / capacity / constraints

M0 凍結本次設定與 checksum
  │
  └─ M4 只使用 topology/capacity 產生 scenario_gt
          │
          ├─ ideal branch (w/o): scenario_gt → M6 decision → M7 validator
          │
          └─ deployment branch (w/):
                scenario_gt + M2 residual → M5 observation → M6 decision → M7 validator

M7 trial facts
  └─ M8 聚合 Risk、Action、Reliability
        └─ M9 產生 15 份可下載表格與可重現性 manifest
```

最重要的隔離原則如下：

```text
M4 scenario_gt 是唯一的驗證真值。
M5 observation 只給 deployment decision 使用，不能當真值。
M2 residual 只進入 deployment branch，ideal branch 絕不讀取它。
M9 只讀 M8 統計，不重新計算指標。
```

---

## 2. 一次 Run 的不可變設定（M0）

API 或 UI 建立 run 時，系統會先建立 `ResolvedRunConfig`。它是此次 run 的不可變設定，會寫入：

```text
M0/experiment_manifest.json
M0/preflight_report.json
M9/reproducibility_manifest.json
M9/run_summary.json
```

目前正式 profile 的預設值：

| 參數 | 值 | 作用 |
|---|---:|---|
| `root_seed` | 114 | 衍生 M4 與 M5 的 deterministic seed |
| `split` | `test` | Perception 正式評估資料切分 |
| `trial_count_per_condition` | 30 | 每個 topology/model/regime 的 paired trial 數 K |
| `scenarios_per_regime` | 8 | 每個 topology/regime 可循環使用的 GT scenario 數 |
| `risk_threshold` | 0.82 | GT 人數／capacity 到此比例即屬需疏散來源 |
| `risk_f_beta` | 2.0 | Risk Consistency 的 F-beta；較重視 Recall |
| `scenario_alpha` | 2.0 | Scenario Beta 分布形狀參數 α |
| `scenario_beta` | 2.0 | Scenario Beta 分布形狀參數 β |
| `rho` | 0.55 | hotspot nodes 合計承載人口的目標比例 |
| `hotspot_selection` | `top_capacity_quartile` | 每個 topology 容量最高的前 25% source nodes 為 H |
| `metric_policy_version` | 2.0.0 | 指標公式版本 |

`root_seed` 不直接把所有亂數變成同一串，而是和 stage、condition、regime、trial 等識別字串組合後 hash 成子 seed。例如：

```text
M4 base seed = hash(root_seed, "M4", topology_id, regime, scenario_index)
candidate seed = hash(M4 base seed, "candidate", attempt_index)
M5 seed = hash(root_seed, "M5", condition_id, regime, trial_index)
```

所以相同 input、相同 run config 會產生相同 scenario 與同樣抽到的 residual；不同 stage 或 trial 不會共用未記錄的亂數狀態。

### 2.1 UI 三個基本參數的意義與影響

UI 設定列中的 `root seed`、`trials / condition`、`scenarios / regime` 分別控制可重現性、誤差抽樣的重複次數，以及空間人群情境的多樣性；三者不是同一件事。

| UI 欄位 | 數值意義 | 改大／改動後的影響 |
|---|---|---|
| `root seed` | M4 scenario generation 與 M5 residual sampling 的 deterministic 亂數起點 | 換 seed 是換一組可重現的抽樣樣本，不代表模型或方法更好。相同 input、完整設定與 seed 必須得到相同 artifacts 與 metrics。 |
| `trials / condition`（K） | 每個 `topology × model × regime` 的 ideal/deployment 配對次數 | K 越大，對 residual 抽樣造成的偶然性平均得越平，指標更穩定；M5～M8 的運算、儲存與記憶體使用量近似隨 K 線性增加。 |
| `scenarios / regime`（S） | 每個 `topology × regime` 產生的不同 `scenario_gt` 數量 | S 越大，可檢驗更多不同空間人群分布。它不會取代 trial residual sampling；每個 deployment trial 仍獨立抽 residual。 |

本系統目前的規模計算為：

```text
configuration_count = topology_count × model_count
paired_trials       = configuration_count × regime_count × K
decision_validations = paired_trials × 2
scenario_gt_count   = topology_count × regime_count × S
```

以目前正式矩陣 `3 topologies × 5 models`、`K = 30`、`S = 8` 為例：

```text
configuration_count  = 3 × 5 = 15
paired_trials        = 15 × 3 × 30 = 1,350
decision_validations = 1,350 × 2 = 2,700
scenario_gt_count    = 3 × 3 × 8 = 72
```

目前執行器會以 `trial_index % S` 輪替選擇 scenario。因此 `K = 30`、`S = 8` 時，每個 scenario 約被使用 3 或 4 次；同一 scenario 的 deployment trials 仍會使用不同的 trial seed 抽取 residual。若 `S > K`，目前 run 只會使用前 K 個 scenarios，故正式實驗應避免設定 `scenarios / regime` 大於 `trials / condition`，或後續將 trial 排程擴充為保證所有 scenario 都至少被使用一次。

M0 也計算 A1、A2、A3，以及每個 topology map/neighbors/rules 檔案的 SHA-256 checksum。輸入檔不存在、數值參數不合法、residual pool 為空時，run 必須在前置檢查失敗，不會自動猜參數或補造資料。

---

## 3. M1：把正式 Perception 資料轉成唯一 canonical 結果

來源檔案：

```text
Data/Perception資料/A1_benchmark_samples_combined.csv
Data/Perception資料/A2_perception_model_registry.csv
Data/Perception資料/A3_model_predictions_raw.csv
```

### 3.1 A1：選出有資格的 sample 與 GT

M1 只保留：

```text
dataset_split == test
count_error_eligible == true
paper_result_eligible == true
```

A1 的 `scene_gt_count` 是 Perception benchmark 的 ground truth。A2 指出每個 model 可用的 dataset；M1 用：

```text
sample_id + compatible_dataset_id
```

找對應 sample。這是 Detection 與 Density 不混用真值的關鍵：Detection model 只配 Detection dataset，Density model 只配 Density dataset。

### 3.2 A3：取得模型預測與 signed residual

只接受：

```text
prediction_status == success
```

每一列 canonical result 都是「一個 model 對一個 sample 的一次預測」：

```text
sample_id, dataset_id, split, paradigm
model_id, model_name, model_version
ground_truth_count, predicted_count
error, absolute_error
ground_truth_regime, predicted_regime
source_ref
```

核心數值定義：

```text
error = predicted_count - ground_truth_count
absolute_error = abs(error)
```

```text
error > 0  代表高估
error < 0  代表低估
error = 0  代表預測剛好相同
```

`ground_truth_regime` 來自 A1，是後續 M2 正式分 pool 的依據。`predicted_regime` 僅用於資料品質檢查與 regime confusion，不可用來建立正式 residual pool。

M1 輸出：

```text
M1/perception_results.csv
M1/perception_results.parquet
M1/perception_results_manifest.json
M1/m1_quality_report.json
M1/excluded_samples.csv
```

manifest 保存 schema、row count、checksum 與 A1/A2/A3 lineage；因此可追查 M2 每個 residual 回到哪一筆正式 prediction。

---

## 4. M2：保留真實 empirical residual，而不是生成假噪音

M2 只能讀：

```text
M1/perception_results.parquet
```

正式 pool key：

```text
model_id + ground_truth_regime
```

例如：

```text
YOLOv8 + LOW
YOLOv8 + MEDIUM
YOLOv8 + HIGH
CSRNet + LOW
CSRNet + MEDIUM
CSRNet + HIGH
```

每個 pool 都保留完整 residual samples：

```text
error_samples.parquet 中每列仍可看到 sample_id、model_id、paradigm、GT regime、residual
```

系統可額外產生 mean、standard deviation、p90 absolute error、min、max 作為描述統計；但是 M5 抽樣時取的是 pool 內的實際 residual 列，不會用 mean/std 合成 Gaussian 或 synthetic perception noise，也不會預設 clipping 或 winsorization。

M2 輸出：

```text
M2/error_samples.parquet
M2/error_distribution_summary.json
M2/regime_statistics.parquet
M2/m2_error_model.json
M2/m2_quality_report.md
```

若某個 model/regime pool 沒有 residual，該 run 失敗；不能把缺失 pool 換成別的 model、別的 paradigm，或填 0。

---

## 5. M3：將 Topology、Capacity 與 Rules canonical 化

每個正式 topology 有三份資料：

```text
*_map_neww.json       節點與 max_occupancy
*_neighbors.json      雙向 adjacency 記錄、方向性 edge cost、traversal cost
*_rule.json           出口、來源／目的地限制、priority rule
```

Topology 的兩個方向語意分開定義：

```text
graph_directionality = undirected
adjacency_semantics = symmetric
edge_cost_directionality = directed
```

`undirected` 只表示區域相鄰關係必須同時存在 `A → B` 與 `B → A`；不要求兩個方向的成本相同。每一筆 neighbors 記錄保留自己的方向性成本，例如 `4 → 6 = 10`、`6 → 4 = 1`。因此 M3 canonical edges 會保留兩筆方向性 edge，而不是把它們合併成一個 cost。

M3 把它們轉成：

```text
nodes: node_id, node_type, capacity, is_source_eligible
edges: source_id, target_id, edge_cost, traversal_cost
adjacency: 每個 source 可以前往的 target
external_exits: 由 topology rule 指定的出口
source_nodes: 非出口且 capacity > 0 的可配置人群節點
```

M3 input contract 會檢查所有 adjacency pair 都有反向記錄、endpoint 存在、cost 為正整數、capacity 與 map 一致；方向性 cost 可以不同。`nearby_zone` 使用各 topology 自己的 external exits 與 max total cost，以方向性 edge cost 執行 Dijkstra 重新 materialize。

並發布：

```text
M3/<topology_id>/topology_spec.json
M3/<topology_id>/topology_nodes.csv
M3/<topology_id>/topology_edges.csv
M3/<topology_id>/topology_rules.json
M3/<topology_id>/validation_report.json
M3/topology_manifest.json
```

M3 會為 canonical topology 與 capacity 分別計算：

```text
topology_checksum
capacity_checksum
```

這兩個 checksum 會往下游寫入 M4 scenario、M5 observation lineage、M6 decision、M7 trial record，確保 paired trial 用的是同一個場域約束。

---

## 6. M4：在沒有 Perception 誤差時建立 Ground-truth Scenario

M4 完全不讀 M1/M2 residual。它只用 M3 的 source node 與 capacity 建立：

```text
scenario_gt_population
```

對每個 topology 和 density regime，先計算：

```text
D_total = round_half_up(total_source_capacity × regime_load_factor)
```

目前 regime load factor：

```text
LOW = 0.25
MEDIUM = 0.55
HIGH = 0.85
```

### 6.1 Hotspot 與 Beta 權重

`H` 是每個 topology 內 capacity 最高的前 25% source nodes。M4 要求：

```text
目標 hotspot 人口 = round_half_up(D_total × rho)
目標一般區域人口 = D_total - 目標 hotspot 人口
```

兩組區域分別以：

```text
weight_i ~ Beta(scenario_alpha, scenario_beta)
```

抽權重，依權重分配人數；若某節點達 capacity，剩餘人數會依既有權重順位分配到仍有容量的節點。候選 scenario 產生後會先執行 M6 feasibility preflight；只有能完成所有高風險 source 合法分配的候選，才會成為正式 scenario。不可行候選會以 deterministic candidate seed 重新抽樣，直到取得固定數量的可行 scenarios。

每個 scenario 最後驗證：

```text
0 <= scenario_gt_population[node] <= capacity[node]
sum(scenario_gt_population) == D_total
rho_actual = sum(population in H) / D_total
```

M4 每列 artifact 包含：

```text
scenario_id, topology_id, ground_truth_regime, D_total
H, rho_requested, rho_actual
scenario_alpha, scenario_beta, scenario_seed_base, scenario_seed
generation_attempt, candidate_rejection_count
candidate_rejection_reason_counts
scenario_gt_population, capacity_check_passed
decision_feasibility_status, decision_feasibility_reasons
scenario_policy_id, scenario_policy_version
m6_decision_policy_version
scenario_checksum, topology_checksum, capacity_checksum
```

輸出：

```text
M4/scenario_gt.jsonl
M4/scenario_manifest.csv
M4/scenario_generator_policy.json
M4/scenario_generation_diagnostics.json
M4/scenario_feasibility_report.json
```

`scenario_checksum` 是 `scenario_gt_population` 的 canonical JSON hash。它是 ideal/deployment 成對比較是否真的使用同一個真值場景的識別證據。

M4 在接受 candidate 為正式 scenario 前會用 M6 planner 執行 ideal decision feasibility preflight。每個高風險 source 的 requested move 必須能依既有 topology priority 分配到合法 target，且不能超過 source 可見人數或非出口 target 的剩餘 capacity。這個 preflight 不呼叫 M7；它只是確認 candidate 在 M6 policy 下可執行。

每個 `topology × regime` 仍必須產生固定數量的正式 scenarios。候選 rejection 不會進入 `scenario_gt.jsonl`、M5 trial 或 `R_ideal`，但會保存於 `M4/scenario_generation_diagnostics.json`，包含 candidate attempts、rejection reason counts、accepted count 與 rejected count。預設最多嘗試 512 次；若仍找不到可行候選，M4 直接失敗，不進入 M5，也不降低 density、capacity 或 M6 規則。

因此 Stage II ideal baseline 是在符合 topology、capacity 與 M6 decision feasibility 的 scenario set 上計算；它不代表任意人口分布都能完成目前的單步決策規則。

---

## 7. M5：只在 Deployment Branch 做 controlled empirical residual propagation

M5 對每個：

```text
condition = topology_id + model_id
regime
trial_index
```

建立一個 `pair_id`，並選取對應 M4 scenario。這個 pair 後續會產生兩條分支：

```text
ideal        = w/o Two-stage framework
deployment   = w/ Two-stage framework
```

只有 deployment 分支會從對應：

```text
model_id + ground_truth_regime
```

的 M2 empirical residual pool，以 `with_replacement` 抽每個 source node 的 residual。M5 寫入的觀測人數是：

```text
observed_population[node]
  = max(0, round_half_up(scenario_gt_population[node] + sampled_residual[node]))
```

這裡的 `floor_at_zero` 只處理「人數不能小於 0」；它不會 clip 到 capacity，也不會施加額外 decision buffer。若 observation 導致不合理的決策，應由 M7 用真值場景做外部驗證，不能在 M5 偷偷把數值修好。

M5 對 deployment trial 儲存：

```text
pair_id, trial_id, scenario_id, scenario_checksum
topology_checksum, capacity_checksum
residual_pool_id, residual_pool_count
sampling_policy, sampling_seed, sampled_residuals
observation_population, observation_checksum
```

ideal branch 只保留指向 M4 scenario 的 lineage，沒有 observation 或 residual。輸出：

```text
M5/observation_trials.csv
M5/observation_trials.parquet
M5/controlled_residual_policy.json
M5/ideal_branch_lineage.json
```

---

## 8. M6：以各自可見的人數產生 evacuation actions

M6 的兩條 branch 輸入不可互換：

```text
ideal / w/o:
  decision_population = scenario_gt_population

deployment / w/:
  decision_population = observed_population
```

Rule-based decision 只看 `decision_population` 與 M3 topology rules。當節點：

```text
decision_population[source] / capacity[source] >= risk_threshold
```

它會建立 action。action schema 固定為：

```text
source_id
target_id
move_count
priority_metadata
```

M6 使用 `capacity_aware_multi_source_rule_based` policy。高風險 source 的 requested move count 沿用：

```text
max(1, round_half_up(decision_population[source] - 0.70 × capacity[source]))
```

並且不超過該 branch 可見的 source population。source 依 utilization ratio 降序、requested move count 降序、natural node ID 升序處理；每次分配都更新 shared target remaining capacity，因此多個 source 不會把同一 target 超額填入。必要時同一 source 可依 priority 順序分配到多個 target。

目標選擇只使用 topology 的既有 `priority_rule = ascending_total_cost`。直接相鄰目的地會依：

```text
total_cost = edge_cost + traversal_cost
```

其中 `edge_cost` 是目前 `source → target` 的方向性成本，`traversal_cost` 是 source node 的通行成本。M6 不使用反向 edge 的 cost 取代目前方向，也不使用 min/max 合併雙向成本。

由小到大排序，依剩餘容量分配；這不是新增一套 pathfinding。`priority_metadata` 同時保存候選目的地、成本、選擇順位、requested/allocated quantity、target remaining capacity 與 allocation order，供 M7 解釋分數。

M6 不會讀 M7 validator 結果，更不能讓 deployment branch 回頭讀 `scenario_gt` 修正 action。輸出：

```text
M6/action_trials.parquet
M6/decision_actions.parquet
M6/m6_manifest.json
```

M6 的 ideal 與 deployment 使用完全相同的 planner；差異只有：ideal 看到 `scenario_gt_population`，deployment 看到 M5 的 `observed_population`。M6 不讀 M7，也不使用 `scenario_gt` 修正 deployment decision。

---

## 9. M7：External Validator 與 Action Components

M7 的概念輸入永遠是：

```text
V(action, topology, scenario_gt)
```

不管 ideal 或 deployment，驗證真值都是 M4 的 `scenario_gt_population`。deployment 的 observation 是 decision 看到的數字，不是世界真實人數。

每筆 M7 trial record 同時保存：

```text
decision_input_mode
decision_input_checksum
validation_truth_source_stage_id = M4
validation_truth_checksum = scenario_checksum
m6_decision_policy_id
m6_decision_policy_version
```

### 9.1 Validator flags

每個 trial 檢查：

```text
invalid_output
unknown_target_violation
forbidden_target_violation
topology_violation
capacity_violation
source_underflow_violation
flow_conservation_violation
rule_violation
valid
violation_reasons
```

其中 post-state 一律以真值計算：

```text
post_population[node]
  = scenario_gt_population[node]
    + incoming[node]
    - outgoing[node]
```

所有非出口 node 都要驗證：

```text
post_population[node] >= 0
post_population[node] <= capacity[node]
```

任何必要 flag 為 true 時：

```text
valid = 0
rule_violation = 1
```

否則：

```text
valid = 1
rule_violation = 0
```

`violation_reasons` 會保留 code、source/target 或 node、實際人數與 capacity 等資料，不能只輸出單一 true/false。

M4 已通過 feasibility preflight 的 ideal trial 必須全部通過 M7。若仍有 ideal validation failure，保留 `M7/decision_validation_trials.parquet` 與 `M7/ideal_invariant_report.json` 作為診斷，但 Run 標記 Failed，且不發布正式 M8/M9。

### 9.2 Risk Consistency

M7 先由 GT 找出真正需要疏散的來源：

```text
expected_high_sources = {
  source | scenario_gt[source] / capacity[source] >= risk_threshold
}
```

再由實際 action 找出獲得建議的來源：

```text
recommended_sources = {
  source | action 格式正確且至少有一筆 evacuation action
}
```

計算：

```text
TP = |expected ∩ recommended|
FP = |recommended - expected|
FN = |expected - recommended|
precision = TP / (TP + FP)
recall    = TP / (TP + FN)
```

正式 Risk Consistency：

```text
F_beta = (1 + beta²) × precision × recall
         / (beta² × precision + recall)
```

`beta` 一律來自 M0 的 `risk_f_beta`，不在程式公式中固定成 2。邊界規則固定：

```text
expected 空、recommended 空  -> 1.0
只有其中一個空              -> 0.0
兩者都非空                  -> 正常 F-beta
```

### 9.3 Action Consistency

正式 Action Consistency 不是 ideal flow overlap。它由三部分組成：

```text
action_consistency_before_gate
  = 0.50 × legality_score
  + 0.35 × priority_score
  + 0.15 × economy_score
```

定義：

```text
legality_score = 1  所有 M7 必要驗證通過
legality_score = 0  任一必要驗證失敗
```

若 `legality_score = 0`，會觸發 fatal gate：

```text
fatal_legality_gate = true
action_consistency = 0
```

Priority Score 用 M6 寫入、且可由 M3 rules 回推的候選目的地成本排名檢查。每筆 action 選到排名第一個可行目的地得 1，否則得 0，再取 trial 平均；evidence 保留來源、候選排序、實際選擇與順位。

Economy Score 對每個真正需要疏散的 source 計算不同 target 的數量：

```text
distinct_target_count <= 3  -> economy_source = 1
distinct_target_count > 3   -> economy_source = 0
```

最後取這些 source 的平均。沒有需要疏散的 source 且沒有 action 時，Economy = 1。若需要疏散但沒有 action，錯誤會反映在 Risk Consistency，不另外發明 Economy 扣分。

為了保留舊分析，M7 也計算 candidate/ideal flow overlap，但它的名稱是：

```text
action_agreement_with_ideal
```

這只是補充分析，不能當正式 Action Consistency。

輸出：

```text
M7/decision_validation_trials.csv
M7/decision_validation_trials.parquet
M7/validator_manifest.json
M7/ideal_invariant_report.json
```

---

## 10. M8：從 Trial Facts 聚合正式指標

M8 不會重新解讀 decision 或改寫 validator facts；它只讀 M7 trial-level records。正式 aggregation key：

```text
topology_id
model_id
ground_truth_regime
framework_condition
decision_interface
metric_policy_version
risk_f_beta
```

rate 的算法：

```text
rate = sum(trial flag) / executed_trial_count
```

連續分數的算法：

```text
score = average(trial-level score)
```

### 10.1 Reliability

Reliability 只由 validator 的 `valid` 決定：

```text
R_trial = valid
R_ideal = average(valid of ideal trials)
R_deploy = average(valid of deployment trials)
Delta_R = R_ideal - R_deploy
```

因此 `R_deploy` 不再是 Valid、Risk、Action 的加權混合分數。Risk Consistency 與 Action Consistency 仍會分開呈現，讓讀者能分辨「是否合法」與「建議是否對準真正高風險區域」。

同一 topology/model/regime 的 w/o、w/ 表格列都帶有同一組配對的 `R_ideal`、`R_deploy`、`Delta_R`；但其他 branch-specific 指標（例如 Risk、Action、violation rate）來自該列所代表的 branch。

GAI 未可用時：

```text
availability = unavailable
executed_trial_count = 0
所有 metric = null
```

這裡的 `unavailable` 只適用於 provider、budget、timeout 或 transport 沒有產生可驗證 terminal decision 的情況。若 GAI 已經回應但 action contract 失敗，或 action episode 無法完成合法分配，M6 會建立 terminal trial fact：

```text
invalid_output:
  valid = 0
  invalid_output = 1
  executed_trial_count = 1

decision_infeasible:
  valid = 0
  m6_decision_infeasible = 1
  executed_trial_count = 1
```

這兩類結果會進入 M8 denominator；它們是 GAI 決策能力的正式觀察，不是 `unavailable`，也不把 M6 failure 說成 M7 topology failure。若 paired ideal/deployment 兩邊都有 terminal outcome，即使 `R_ideal = 0`，paired row 仍為 `available` 並保留原始數值。

`R_ideal` 不直接硬編碼為 1；它仍由 M7 `valid` trial facts 聚合。對人工 rule source 的成功正式 Run，M4 feasibility preflight 與 M7 ideal invariant gate 使 `R_ideal = 1` 成為預期基準。對 AI rule source，`R_ideal < 1` 也可能是 AI rule bundle 與人工 M7 gold-standard 不相容的正式結果，不應直接視為 perception regression。

若同一 paired group 出現：

```text
R_ideal = 0
R_deploy = 0
Delta_R = 0
```

`Delta_R = 0 - 0` 在數學上正確，但不代表 perception residual 沒有影響。它表示 ideal branch 已全部未通過 M7，已沒有可觀察的額外可靠度下降空間。UI 必須標示 `Ideal baseline failed`，並要求使用者回到 M7 violation evidence 判讀原因。若 `0 < R_ideal < 1`，UI 標示 `Ideal baseline is partial`，Delta R 只能作為不完整 ideal baseline 上的條件性比較。

若 `R_ideal = 0` 但 `R_deploy > 0`，UI 也標示 `Ideal baseline failed`。此時可能出現負的 `Delta_R`，但不得解讀為 perception residual 改善了結果；它只表示 deployment branch 的部分輸出通過，而 ideal branch 沒有提供有效的完整 reference point。

輸出：

```text
M8/decoupled_2_stage_metrics.csv
M8/decoupled_2_stage_metrics.parquet
M8/metrics_manifest.json
```

---

## 11. M9：凍結論文用結果與可重現性證據

M9 只讀 M8 的 canonical metric rows，不能重新計算 Risk、Action 或 Reliability。每個 topology/model configuration 會輸出一份 CSV 與 Markdown，總計 15 份；另有全體資料表與下載 bundle：

```text
M9/decoupled_2_stage_<topology_id>__<model_id>.csv
M9/decoupled_2_stage_<topology_id>__<model_id>.md
M9/decoupled_2_stage_all_tables.csv
M9/decoupled_2_stage_all_tables.md
M9/decoupled_2_stage_all_tables.xlsx
M9/decoupled_2_stage_all_tables.zip
M9/insight_report.md
M9/insight_summary.json
M9/delivery_manifest.json
M9/reproducibility_manifest.json
M9/run_summary.json
```

`insight_report.md` 是由固定程式產生的白話結果整理，資料流為：M8 canonical metrics 作為數值來源，M9 all tables 作為發布列數檢查，M7 trial validation evidence 解釋違規，M6 GAI trace 解釋模型輸出與執行狀態。它只描述本次 Run，不改寫 M8，也不自動宣稱因果關係。

`insight_summary.json` 是同一份報告的結構化版本，供 UI 或後續分析工具讀取。它包含 paired comparison、LOW/MEDIUM/HIGH 趨勢、M7 violation counts、GAI status counts、lineage 檢查與來源 artifact checksum。`invalid_output`、`decision_infeasible` 與 `unavailable` 會分開呈現。

報告中的趨勢文字是描述性判斷：例如 `R_deploy non-increasing` 只代表本次 Run 從 LOW 到 HIGH 沒有上升，不表示程式強制該趨勢，也不能單獨支持因果結論。若 `R_ideal = 0`，報告會要求回到 M7 evidence，而不把 `Delta_R = 0` 解釋為沒有 perception 影響。

表格至少呈現：

```text
Framework Condition, Decision Interface, Regime
Risk Precision, Risk Recall, Risk Consistency
Legality, Priority, Economy, Action Consistency
Invalid / Rule / Capacity / Topology violation rates
R_ideal, R_deploy, Delta_R
metric_policy_version, risk_f_beta
```

`reproducibility_manifest.json` 保存 M1、M2、M4、M5、M6、M7、M8 artifact checksum，以及 M0 的完整 config。這讓任何一個 M9 數值都能一路回溯到：

## 12.7 Perception Error Boundary

系統提供唯讀的 `Perception Error Boundary` counterfactual sensitivity analysis，針對目前選定的：

```text
Topology × Perception model × Regime × Rule Source × 決策方式
```

它不是新的 M8/M9 metric，也不會修改正式 M5、M7、M8、M9 artifact。分析只讀取已發布 Run 的 `M4/scenario_gt.jsonl`、`M5/observation_trials.parquet`、M3 topology 與 M7 validator contract。

### Analytical boundary

M6 high-risk 判斷為：

```text
observed_population / capacity >= risk_threshold
```

因此每個 source 可計算：

```text
first_high_risk_count = ceil(risk_threshold × capacity)
last_non_high_count = first_high_risk_count - 1
signed_error_boundary = last_non_high_count - ground_truth_population
```

這個 boundary 只表示 M6 risk classification 與 requested move count 的整數門檻，不保證 M7 一定通過。M7 仍會獨立檢查 capacity、topology、source underflow 與 flow conservation。

### Existing Run Analysis 與 Boundary Sweep

系統保留正式 M5 每個 trial 的完整 `sampled_residuals`，不重新抽樣，也不合成 Gaussian 或其他 synthetic noise。開啟功能時先做 `Existing Run Analysis`，直接讀取 M5 observation 與 M7 trial facts，顯示 `Observed Estimate`：目前 R_deploy、成功／失敗數量、MAE、P90、最大誤差與觀察到的成功／失敗誤差區間。這不是精確臨界值；若兩類誤差重疊，失敗可能還與誤差位置、方向、scenario 或 topology margin 有關。

使用者明確啟動 `Boundary Sweep` 後，才以固定 lambda grid 重跑 Rule-based M5→M6→M7。誤差縮放參數 lambda 定義為：

```text
α = 0：observed_population = scenario_gt，代表 ideal input
α = 1：observed_population = formal M5 observation
```

中間值使用：

```text
observed_α = max(0, round_half_up(scenario_gt + α × sampled_residual))
```

v1 固定評估 `lambda=0.00, 0.05, ..., 1.00`，重用同一個 scenario、residual、seed、topology 與 M7 human gold-standard validator。每個點保存 R_deploy、valid/executed、MAE、RMSE、STD、P90、最大誤差、低估／高估比例與 violation reason。Boundary 目標固定為 `R_deploy > 0`、`>=0.50`、`>=0.80`、`>=0.95`。

```text
required_residual_reduction = 1 - max_acceptable_alpha
```

此值是 residual magnitude 需要降低的比例，不是 perception accuracy 必須提升的百分比。

安全邊界是從 lambda=0 開始、直到該點以前每個已測試點都達標的最大 lambda；若 lambda=1 仍達標，狀態為 `ABOVE_SEARCH_RANGE`。曲線有上升段時標示 `NON_MONOTONIC_RELIABILITY_CURVE`，但仍使用 conservative safe boundary。若 `R_ideal = 0`，UI 與下載報告會顯示：

```text
Ideal baseline failed — perception boundary is not interpretable
```

因為正確人口輸入已無法通過 M7，沒有足夠的 ideal headroom 可以把後續改善歸因於 perception residual。GAI 若沒有可重播的 action episode，Existing Run Analysis 仍可讀取已保存 facts，但 Boundary Sweep v1 不會自動重呼叫 GAI，也不使用 Rule-based action 代替。

分析 endpoint：

```text
GET /api/v1/decoupled-2-stage-experiment/runs/{run_id}/perception-error-boundary
```

支援 `format=json` 與 `format=md` 下載。所有下載內容均標示：

```text
COUNTERFACTUAL ANALYSIS — NOT FORMAL M8/M9 RESULT
```

```text
run_id → condition_id → pair_id → trial_id
→ scenario_id / scenario checksum
→ topology/capacity checksum
→ model/regime residual pool
→ sampling seed / sampled residuals
→ action → validator evidence → M8 aggregate
```

其中 M0 manifest 也會在 M4 diagnostics materialize 後回填：

```text
scenario policy id/version
feasibility-constrained sampling flag
max candidate attempts
M4/scenario_generation_diagnostics.json checksum
```

因此 M0 雖然先於 M4 建立，仍能保存完整的 scenario-generation lineage。

---

## 12. UI 如何對應這套流程

UI 維持一鍵執行與結果導向，但必須依照指標的實際聚合層級，將結果區分為：

```text
1. Run 設定
2. Ideal Baseline
3. Deployment 結果矩陣
4. Selected Configuration Detail
5. Paper View 與下載
6. 進階資訊
```

整體畫面結構：

```text
Run 設定列
  └─ root seed、trials、scenarios、Risk Consistency β

Ideal Baseline
  └─ Topology × Regime 的 R_ideal

Deployment 結果矩陣
  └─ Model × Topology 的 deployment metrics

Selected Configuration Detail
  └─ 所選 topology × model × regime 的
     R_ideal、R_deploy、Delta R 與 validator evidence

Paper View
  └─ 所選 topology × model 的完整 M8 rows 與表格下載

進階資訊
  └─ scenario policy、risk threshold、metric policy version、
     decision interface 與 GAI availability
```

使用者調整 `Risk Consistency β` 時會送出新 run；舊 run 的 manifest 與結果不得直接改寫。UI 中所有數值只讀取 M8 canonical metrics，不得在前端重新定義、推導或改寫實驗公式。

### 12.1 Run 設定列

UI 設定列維持：

```text
root seed
trials / condition
scenarios / regime
Risk Consistency β
```

並顯示：

```text
metric policy version
decision interface
run status
```

`Risk Consistency β` 是 run-level 參數。使用者修改 β 後必須建立新 run，且該值必須與本次結果的 `risk_f_beta` 一致。

同一個 Deployment 結果矩陣中的所有 cell 必須來自：

```text
相同 run_id
相同 risk_f_beta
相同 metric_policy_version
相同 decision_interface
```

不同 β 或不同 metric policy 的結果不得混入同一個矩陣。

### 12.2 Ideal Baseline by Rule Source

`R_ideal` 不放入 Model × Topology 的 Deployment 結果矩陣，而是在矩陣上方以兩張獨立矩陣顯示。兩張矩陣使用相同的 topology × regime 座標，但 rule source 不混合；矩陣上方另有獨立的決策方式選擇：

```text
用 rule-based 做決策
用 GAI 做決策
```

兩張矩陣會同步套用目前選擇的決策方式：

```text
Human Rule Ideal Baseline
Scope: human_manual_v1 × selected decision interface × topology × regime

AI-generated Rule Ideal Baseline
Scope: ai_generated_derived_v1 × selected decision interface × topology × regime
```

建議表格：

| Topology | LOW | MEDIUM | HIGH |
|---|---:|---:|---:|
| FCU Campus | R_ideal | R_ideal | R_ideal |
| Taichung Lantern Festival | R_ideal | R_ideal | R_ideal |
| Taipei New Year’s Eve | R_ideal | R_ideal | R_ideal |

每一格顯示：

```text
R_ideal = <value>
n = <executed ideal trial count>
```

例如：

```text
R_ideal = 1.000
n = 30
```

Human 矩陣使用 `human_manual_v1 + selected decision interface + w/o + ideal`；AI 矩陣使用 `ai_generated_derived_v1 + selected decision interface + w/o + ideal`。新 Run 的 ideal action scope 固定為：

```text
rule source × topology × regime × scenario/trial
```

其中刻意排除 `model_id`。因為 w/o ideal 的 M6 input 直接是 M4 `scenario_gt`，沒有讀取 M5 residual 或 model-specific observation，所以同一 rule source、decision interface、topology、regime 與 trial 只應執行一個 ideal decision episode。Perception model 的差異只在 w/ deployment branch 透過 M5 observation 進入。

M8 仍可在每個 model-specific paired row 中攜帶同一個 `R_ideal`，以便 Paper View 依 model 顯示完整配對；這些是對共同 baseline 的引用，不是五次獨立 ideal 計算。新 Run 的 M6/M8 manifest 會保存 `ideal_baseline_scope` 與 `ideal_action_scope` 供追溯。

歷史 Run 若建立於共用 ideal action 版本之前，可能仍有每個 model 各自的 GAI ideal 結果。UI 會檢查這些舊 row：若數值不一致，顯示 `Baseline consistency error`，不取平均、不改寫歷史 artifact，並引導使用者到 Paper View 查看各 model 的原始 paired rows。這是歷史資料版本差異提示，不代表新 Run 仍會依 model 分別呼叫 GAI ideal。

Human 與 AI Ideal Baseline 都不受 Model filter 影響。使用者切換 YOLOv8、CSRNet 或其他 perception model 時，同一 rule source × decision interface × topology × regime 的 baseline 不得跟著改變。AI baseline 的 `R_ideal` 是 AI rule source 在人工 M7 gold-standard 下的 ideal validity，不是 Perception accuracy，也不取代 Human baseline。

完整 baseline 維度為：人工規則／AI 生成規則 × Rule-based／GAI。GAI 若沒有 provider、budget 或 transport terminal result，該矩陣格顯示 `unavailable`，不填 0；若 GAI 已執行但輸出為 `invalid_output` 或 `decision_infeasible`，則該 trial 是正式 `valid=0`，M8 的 `R_ideal` 仍照實呈現。`R_ideal = 0` 時，使用者不得將 `Delta R` 解讀成 perception residual 造成的下降，必須回看 M6/M7 evidence。

### 12.3 Deployment Comparison Index

Deployment Comparison Index 是 5 個 perception models × 3 個 topologies 的快速選擇入口，不是八組比較的完整結果表。矩陣提供 Rule Source 與「決策」selector，可在人工／AI 生成規則，以及 rule-based／GAI 決策方式之間切換。

矩陣固定讀取：

```text
framework_condition = w/ Two-stage framework
decision_interface  = selected decision interface
rule_source_id      = selected_rule_source
```

矩陣維度：

```text
Rows    = perception model
Columns = topology
```

使用者選擇：

```text
決策 = 用 rule-based 做決策 / 用 GAI 做決策
Regime = LOW / MEDIUM / HIGH
Metric
```

矩陣允許選擇的 Metric 只有：

```text
R_deploy
Delta R
Risk Consistency
Action Consistency
Invalid Output
Rule Violation
```

矩陣不可提供：

```text
R_ideal
Valid Rate
```

原因是：

```text
R_ideal
→ topology × regime 的 ideal baseline
→ 不屬於 model-dependent deployment comparison

Valid Rate
→ 是 R_deploy 的 validator-side 計算依據
→ 不應被顯示為另一個獨立可靠度
```

每個 cell 顯示：

```text
metric value
n = executed deployment trial count
```

例如：

```text
R_deploy
0.700
n = 30
```

切換 Regime 或 Metric 只改變 UI 讀取的 M8 aggregate row 與欄位，不建立新 run，也不重新計算 metric。

### 12.4 Selected Configuration Detail

使用者點選 Deployment Comparison Index 中的一個 cell，或點選 Paper View 的任一 canonical row 後，UI 顯示：

```text
Selected Configuration Detail
Topology × Model × Regime × Rule Source × 決策方式
```

例如：

```text
FCU Campus
YOLOv8
HIGH
Human manual rules
Rule-based
```

若從 Paper View 點選 AI generated rules 或 GAI row，詳細區會切換到該規則來源 × 決策方式。目前 GAI execution mode 為 reserved/unavailable，只顯示 unavailable，不以人工 Rule-based 數值代替。

第一區顯示 Reliability Comparison：

畫面先用一小段白話說明這一區回答的問題：在同一批 scenario/trial 下，決策輸入從正確的 `scenario_gt` 改成含 Perception 觀測誤差的 `observed_population` 後，M7 通過比例是否下降。這裡只比較可靠度，不把 Risk 或 Action 指標重新加權進來。

Selected Configuration Detail 不另外顯示 `Ideal branch`、`Deployment branch` 與 `Availability` 三個執行狀態欄位；若發生 `invalid_output`、`decision_infeasible` 或 `unavailable`，仍以必要 Alert 顯示原因。Paper View 配對表格的 `M6 Outcome` 與 `Availability` 可選欄位不受此 UI 調整影響。

```text
Ideal branch
R_ideal = 1.000
Validator valid rate = 1.000
n = 30

Deployment branch
R_deploy = 0.700
Validator valid rate = 0.700
n = 30

Delta R = 0.300
```

畫面不得再另外建立一張獨立的 `Valid Rate` 主指標卡。

應以以下方式呈現：

```text
R_deploy
0.700
Validator valid rate
n = 30
```

而不是：

```text
R_deploy = 0.700
Valid Rate = 0.700
```

因為兩者在目前 reliability 定義下是同一個數值：

```text
R_deploy = average(valid of deployment trials)
```

若 M8 或 API 已提供 numerator 與 denominator，可以顯示：

```text
R_deploy = 21 / 30 = 0.700
```

若目前只提供 rate 與 executed trial count，則顯示：

```text
R_deploy = 0.700
Validator valid rate
n = 30
```

不得由前端使用浮點數反推並自行製造 validator pass count。

第二區顯示 Consistency：

畫面說明：這一區用來看 M6 是否找對真正高風險來源，以及 action 是否符合合法性、優先順序與經濟性規則；它是 M6 決策品質的診斷資訊，不是另一個 `R_deploy`。

```text
Risk Consistency
Risk Precision
Risk Recall
Risk β

Action Consistency
Legality
Priority
Economy
```

例如：

```text
Risk Consistency = 0.842
Precision = 0.781
Recall = 0.913
β = 2.0

Action Consistency = 0.781
Legality = 0.833
Priority = 0.721
Economy = 1.000
```

第三區顯示 Failure Breakdown：

畫面說明：這一區把沒有通過的原因拆開。`Invalid Output`、`M6 Contract Violation`、`M6 Decision Infeasible` 屬於 M6 輸出或分配問題；`Rule Violation`、`Capacity Violation`、`Topology Violation` 屬於 M7 對 action 的獨立驗證結果。各欄是該類問題的 rate，不是要相加成一個總分。

```text
Invalid Output
Rule Violation
Capacity Violation
Topology Violation
```

每個 rate 同時顯示：

```text
rate
executed trial count
```

若 canonical artifact 已提供 occurrence count，也可以顯示：

```text
1 / 30
0.033333
```

若沒有 occurrence count，只顯示既有 canonical rate，不得由 UI 自行重算。

### 12.5 R_deploy 與 Valid Rate 的一致性檢查

UI 雖然不把 `Valid Rate` 顯示成獨立主指標，但仍應使用它進行資料一致性檢查。

在目前的 reliability 定義下：

```text
R_deploy = deployment valid rate
R_ideal  = ideal valid rate
```

因此 UI 收到資料後必須檢查：

```text
R_deploy == deployment valid rate
R_ideal  == ideal valid rate
```

比較時可使用系統統一的小數精度容許值。

若資料不一致，不得同時顯示兩個不同數字，也不得由 UI 選一個覆蓋另一個。應顯示：

```text
Metric consistency error

R_deploy 與 deployment valid rate 不一致。
請檢查 M7 trial facts、M8 aggregation 或 metric policy version。
```

同理，若 `R_ideal` 與 ideal valid rate 不一致，也應顯示資料錯誤。

這項檢查只用於發現資料不一致，不會改變 canonical metric。

### 12.6 Paper View

點選 Deployment 結果矩陣 cell 後，Paper View 鎖定相同的：

```text
topology_id
model_id
```

並顯示該 configuration 的完整 M8 rows。

Paper View 篩選器維持：

```text
Rule Source
決策方式
Regime
```

選項：

```text
Rule Source
  ALL
  人工規則
  AI 生成規則

決策方式
  ALL
  用 rule-based 做決策
  用 GAI 做決策

Regime
  ALL
  LOW
  MEDIUM
  HIGH
```

`ALL` 只代表顯示所有符合條件的 paired rows，不做平均、加總、合併或重算。

Paper View 不再使用獨立的 Paired Reliability Summary 卡片，而將配對結果直接放進表格。每個：

```text
Topology × Perception model × Regime × Rule Source × 決策方式
```

只產生一列，固定代表：

```text
w/o（framework_condition = w/o Two-stage framework、trial_type = ideal）
↔
w/（framework_condition = w/ Two-stage framework、trial_type = deployment）
```

這兩側共用同一組 scenario/trial；w/o 的 M6 input 是 `scenario_gt`，w/ 的 M6 input 是 `observed_population`。表格固定顯示：

```text
R_ideal
R_deploy
Delta R
```

在人工-only profile 中，這三個值來自同一 topology × model × regime 的人工 Rule-based paired rows；在 rule source comparison profile 中，則依每個 rule source × M6 interface 各自建立一列。若 paired rows 缺少或不一致，三個欄位顯示 `unavailable` 或 `consistency error`，不得平均、推導或補值。

其他欄位可用「Paper View 表格欄位」Checkbox 選擇是否顯示。Risk、Action、Failure 固定取 deployment branch，並在欄位名稱標示 scope：

```text
Risk 指標
Action 指標
Failure 指標
Executed Trials：ideal n / deployment n
M6 Outcome：ideal status / deployment status
Availability：ideal / deployment
Metric Policy
```

Checkbox 只控制 UI 閱讀層，不改變篩選條件、不重新計算 M8，也不把 deployment diagnostics 當成 paired reliability。每列的 Delta R 下方會在需要時顯示 interpretation status：

- `Unavailable`：沒有正式 M7/M8 metrics，不以 0 代替。
- `Consistency error`：paired rows 缺少或不一致，暫不解讀 Delta R。
- `Ideal baseline failed`：`R_ideal = 0`；若 `R_deploy = 0`，Delta R 沒有可解讀的額外可靠度空間；若 `R_deploy > 0`，可能出現負 Delta R，也不得解讀為 perception residual 改善。
- `Ideal baseline is partial`：`0 < R_ideal < 1`；Delta R 是條件性比較。
- `Interpretable paired comparison`：可在相同配對內解讀 ideal/deployment 落差。

Paper View 表格固定欄位順序為：

```text
Rule Source
實驗情境
決策方式
Regime

R_ideal
R_deploy
Delta R

Risk Precision
Risk Recall
Risk Consistency
Risk β

Legality
Priority
Economy
Action Consistency

Invalid Output
Rule Violation
Capacity Violation
Topology Violation

Executed Trials
M6 Outcome
Availability
Metric Policy
```

UI 將資料列篩選器呈現為：`Rule Source`、`決策方式`；Topology、Perception model、Regime 是 Paper View 的獨立條件選擇器。Framework／Trial Type 不再作為獨立篩選器，因為每列固定是 `w/o ↔ w/`。其餘可選欄位各自提供 Checkbox，分為核心結果、Risk、Action、Failure 與治理資訊五組；`R_ideal`、`R_deploy`、`Delta R` 永遠顯示。預設顯示 `Executed Trials`、`M6 Outcome`、`Availability`；「全部欄位」與「只看核心結果」只改變表格欄位，不改變資料列、數值或 M8 aggregation。

其中：

- `R_ideal` 固定讀取 w/o ideal row；`R_deploy` 固定讀取 w/ deployment row；`Delta R` 固定讀取 M8 canonical `delta_r`。
- `Executed Trials`、`M6 Outcome`、`Availability` 以 `ideal / deployment` 並列，讓使用者先確認配對是否完整。
- Risk、Action、Failure 欄位固定讀取 deployment row，不代表兩側重新聚合。
- `gai_reserved` 的 metrics 顯示 `unavailable`，不得把 null 轉成 0。
- 若 AI rule source 的 `R_ideal` 與 `R_deploy` 都是 0，保留三個 M8 數值，但不得將 `Delta R = 0` 解讀為沒有 perception degradation。

`gai_reserved` 在目前 reserved/unavailable mode 顯示：

```text
availability = unavailable
metric = null
executed_trial_count = 0
```

不得以 0 取代，也不得混入 Rule-based 統計。

### 12.7 UI 資訊層級

UI 採用「一個區塊回答一個問題」的資訊層級，依序回答四個不同問題。

第一層：

```text
Ideal Baseline by Rule Source
```

回答：

```text
在沒有 perception residual 影響時，
人工規則與 AI 生成規則各自的 decision baseline 是否可靠？
```

兩個 rule source 必須分開顯示，不合併成一個 baseline。每格只顯示 `R_ideal`、實際 trial 數與 interpretation status。

第二層：

```text
Deployment Comparison Index
```

回答：

```text
不同 perception model 的 empirical residual
進入不同 topology 後，deployment 結果有何差異？
```

此矩陣固定為「目前選定決策方式 + w/ + deployment」，可在 `用 rule-based 做決策` 與 `用 GAI 做決策` 之間切換；它只作為快速選擇入口，其他指標透過 Metric 選單切換，不全部放在同一格。

第三層：

```text
Selected Configuration Detail
```

回答：

```text
該 cell 所選 rule source × 決策方式的 R_ideal、R_deploy、Delta R、Risk、Action
與 violation 結果是如何組成？
```

第四層：

```text
Paper View
```

回答：

```text
同一組條件下，八組原始 M8 rows 與四組 paired reliability summary 如何比較？
```

主畫面預設只顯示主要欄位；Risk、Action、Failure 與 Metric Policy 等詳細欄位按需展開。資料仍完整保留，UI 不重新計算 M8 指標。

不得將四個問題混在同一個矩陣中。

### 12.8 建議畫面配置

```text
┌─────────────────────────────────────────────────────────────┐
│ Run Settings                                                │
│ Seed | Trials | Scenarios | Risk β | Policy Version        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Ideal Baseline by Rule Source                              │
│ Human rules | AI-generated rules · Topology × Regime        │
│                                                             │
│ Topology                  LOW       MEDIUM       HIGH        │
│ FCU Campus                ...       ...          ...         │
│ Taichung Lantern Festival ...       ...          ...         │
│ Taipei New Year’s Eve     ...       ...          ...         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Deployment Comparison Index                                 │
│ Rule source [Human]  Regime [HIGH]  Metric [R_deploy]       │
│                                                             │
│ Model       FCU Campus   Taichung Festival   Taipei NYE     │
│ YOLOv8         ...              ...              ...         │
│ YOLOv11        ...              ...              ...         │
│ RT-DETR        ...              ...              ...         │
│ CSRNet         ...              ...              ...         │
│ MCNN           ...              ...              ...         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Selected Configuration Detail                               │
│ FCU Campus × YOLOv8 × HIGH                                  │
│                                                             │
│ R_ideal         R_deploy         Delta R                    │
│ ...             ...              ...                        │
│                                                             │
│ Validator evidence                                          │
│ Ideal valid rate      ...     n = ...                       │
│ Deployment valid rate ...     n = ...                       │
│                                                             │
│ Risk / Action / Failure Breakdown                           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Paper View | Trial Evidence | Downloads                     │
└─────────────────────────────────────────────────────────────┘
```

### 12.9 UI 顯示規則

```text
R_ideal
Scope = topology × regime × decision interface

R_deploy
Scope = topology × model × regime × decision interface

Delta R
Scope = topology × model × regime × decision interface

Risk Consistency
Scope = topology × model × regime × decision interface

Action Consistency
Scope = topology × model × regime × decision interface

Invalid Output
Scope = topology × model × regime × decision interface

Rule Violation
Scope = topology × model × regime × decision interface

Valid Rate
Scope = validator evidence
不作為 Deployment 結果矩陣的獨立 metric
```

每個 tooltip 必須顯示：

```text
Metric name
Metric scope
Framework condition
Regime
Executed trial count
Metric policy version
Risk β（Risk Consistency 時）
```

### 12.10 結果矩陣與 Paper View 閱讀方式

Deployment Comparison Index 只負責提供目前所選 Rule Source × 決策方式的 deployment 快速選擇入口；它不取代 Paper View 的完整 paired comparison。

矩陣固定顯示：

```text
framework_condition = w/ Two-stage framework
decision_interface  = selected decision interface
rule_source_id      = selected rule source
```

矩陣列為 perception model，欄為 topology。使用者選擇 ground-truth regime 與 deployment metric；每個 cell 顯示一個 M8 aggregate 值及 executed trial count。

矩陣可選 metric：

```text
R_deploy
Delta R
Risk Consistency
Action Consistency
Invalid Output
Rule Violation
```

其中每個 cell 的資料列必須同時符合：

```text
framework_condition = w/ Two-stage framework
trial_type = deployment
decision_interface = 目前選定的決策方式
rule_source_id = 目前選定的 Rule Source
```

`R_ideal`、`R_deploy`、`Delta R` 在 Paper View paired row 固定顯示；Selected Configuration Detail 仍提供同一條件的 Reliability Comparison 與 validator evidence。

點選 cell 後：

```text
1. 讀取相同 topology × model × regime、Rule Source 與決策方式的 deployment row。
2. 讀取相同 Rule Source × 決策方式的 ideal row。
3. 顯示該決策方式與 Rule Source 配對的 R_ideal、R_deploy 與 Delta R。
4. 顯示 Risk、Action 與 violation breakdown。
5. 提供 Paper View 與下載入口。
```

Paper View 的篩選只控制列的顯示，不影響 Deployment 結果矩陣，也不得重新計算 aggregate。每個 paired condition 只顯示一列，固定包含 `R_ideal`、`R_deploy`、`Delta R`；Risk、Action、Failure 則明確標示為 deployment diagnostics。

在 rule source comparison profile 中，Paper View 另提供 `Rule Source` 與決策方式篩選，並以 paired row 顯示：人工/AI 生成規則 × Rule-based/GAI。每列只使用同一 rule source × interface 的 ideal/deployment pair；AI 生成規則的 `R_ideal` 是其決策在人工 M7 gold-standard 下的 ideal validity，不是 perception accuracy。GAI unavailable 時顯示 unavailable/null。

Paper View 的欄位與比較方式另整理於 [`paper_view_reading_guide.md`](paper_view_reading_guide.md)。

## 14. Perception Error Tolerance Boundary

目前 Boundary 分析分成兩種模式，並且都限定在目前選定的 topology、model、regime、rule source、決策方式與 deployment framework：

### Existing Run Analysis（預設）

開啟 Selected Configuration Detail 的 Boundary 區塊時，系統先直接讀取已發布 Run 的 M4 `scenario_gt`、M5 `observation_trials` 與 M7 deployment trial facts。這個模式不重跑 M5～M8，標示為 `Observed Estimate`，用來快速回答：

- 目前 `R_deploy` 與成功／失敗 trial 數量。
- 成功 trial 中觀察到的最大誤差。
- 失敗 trial 中觀察到的最小誤差。
- MAE、P90、最大絕對誤差、低估／高估比例。
- 主要 M7 violation reason。

這是已觀察資料的區間，不是精確臨界值。若成功與失敗誤差重疊，系統會說明失敗可能還受誤差位置、正負方向、scenario 或 topology margin 影響。

### Boundary Sweep Run

只有使用者按下「開始 Boundary Sweep」才建立獨立背景 Job。Sweep 固定使用 `lambda = 0.00, 0.05, ..., 1.00`：

```text
observed_population(lambda)
  = max(0, round_half_up(scenario_gt + lambda × sampled_residual))
```

`lambda=0` 是正確人數輸入，`lambda=1` 是正式 M5 observation。每個 lambda 重用同一批 scenario、residual sample、seed、topology 與 capacity，依序執行：

```text
scaled M5 observation → Rule-based M6 → M7 human gold-standard → boundary aggregate
```

目前 v1 固定只支援 Rule-based Sweep；GAI 若有既有 trial facts，可以做 Existing Run Analysis，但不會為 Boundary Sweep 重新呼叫 Ollama 或其他 GAI。

若粗略曲線首次觀察到 `R_deploy = 0`，系統會額外建立 focus curve：從 `lambda=0.00` 到第一個零可靠度點，使用 `step=0.01` 細看臨界區間。完整 `lambda=0.00～1.00、step=0.05` 粗略曲線仍保留，避免遺失非單調或後續恢復證據。這是顯示與局部解析度的補強，不代表可以把第一個零點直接當成精確數學臨界值。

每個 lambda 保存 `R_deploy`、valid/executed、MAE、RMSE、STD、P90、最大誤差、低估／高估比例與 violation reason。固定檢查：

```text
R_deploy > 0
R_deploy >= 0.50
R_deploy >= 0.80
R_deploy >= 0.95
```

`safe_critical_lambda` 是從 lambda=0 開始、直到該點以前每一個已測試點都達到目標的最大值；所需降低 residual 為：

```text
max(0, 1 - safe_critical_lambda) × 100%
```

這個百分比代表 residual magnitude reduction，不是模型 accuracy 提升百分比。若 lambda=1 仍達到目標，狀態為 `ABOVE_SEARCH_RANGE`；若曲線中間上升，保留結果並標示 `NON_MONOTONIC_RELIABILITY_CURVE`，主要結果仍使用 conservative safe boundary。若 `R_ideal=0`，狀態為 `BASELINE_FAILED`，不得把後續改善解讀為 perception error 的可恢復邊界。

### Boundary artifact 與相容性

Boundary Job 不修改來源 Run。結果寫入：

```text
published/runs/{source_run_id}/boundary_analysis/{boundary_job_id}/
```

包含 `boundary_config.json`、`source_lineage.json`、`lambda_curve.csv`、`boundary_focus_curve.csv`、`boundary_targets.json`、`trial_results.jsonl`、`monotonicity_audit.json`、`boundary_summary.json` 與 `boundary_report.md`。這些檔案是 counterfactual analysis，不是正式 M8/M9 結果，下載內容會明確標示：

```text
COUNTERFACTUAL ANALYSIS — NOT FORMAL M8/M9 RESULT
```

目前 API 仍保留 `/runs/{run_id}/perception-error-boundary` 作為 Existing Run Analysis 相容入口，另提供 Boundary capability、建立 Job、Job status、curve、summary、trial 與下載 endpoint。Boundary 與 Paper View、Deployment Comparison Index、Selected Configuration Detail 的正式 M8 數值互不覆寫。

若篩選組合沒有任何 canonical row，UI 顯示：

```text
目前篩選沒有符合結果
```

不得顯示 0 或使用其他條件的資料代替。

### 12.11 Failed Run 與舊結果清除

若 M4 在最大 candidate attempts 內找不到可行 scenario，或 M7 的 ideal
invariant 失敗：

```text
API status = 409
Run status = FAILED
failure payload = run_id + stage_id + message + failure_details
正式 M8/M9 = 不發布
```

API `/runs` 仍會列出 Failed Run summary。UI 收到新的 Failed Run 後會清除上一個
成功 Run 的 `run` 與 selected configuration state，避免畫面繼續顯示舊的 Ideal
Baseline；若有 report path，提供 M4/M7 diagnostics 下載入口。

---

## 13. 讀結果時應避免的誤解

```text
w/o 不代表「沒有跑 Perception」：它是同一 GT scenario 的理想決策基線。
w/ 不代表系統自動修正誤差：它是把真實 residual 帶入 observation 後的部署評估。
observation 不是 ground truth；M7 一律用 scenario_gt 驗證。
R_deploy 不是 Risk/Action 的加權總分；它只是 deployment valid rate。
GAI unavailable 不是 0 分，也不代表 Rule-based 的結果。

## 13. 人工／AI 規則來源 × M6 介面比較 profile

除既有人工-only profile 外，系統另提供 `decoupled_2_stage_rule_source_comparison_v1`。它沿用同一套 M0～M9 orchestration，不建立另一個只供比較用的 runner。每個 topology × perception model × ground-truth regime 會保留八列原始 M8 aggregate rows：

| Rule source | M6 interface | Framework |
|---|---|---|
| 人工規則 | Rule-based | w/o / w/ |
| 人工規則 | GAI | w/o / w/ |
| AI 生成規則 | Rule-based | w/o / w/ |
| AI 生成規則 | GAI | w/o / w/ |

`w/o` 是 ideal branch，M6 input 為 `scenario_gt`；`w/` 是 deployment branch，M6 input 為同一組配對 trial 的 `observed_population`。兩種 rule source 不重新產生 scenario，也不重新抽 residual：scenario cohort 以 `topology × regime × scenario_index` 配對，observation 以 `topology × model × regime × trial_index` 配對，因此 rule source 差異不會混入另一批人口或 perception 抽樣。

M4 先在共同 human topology／capacity contract 上執行介面無關的 deterministic feasibility oracle；只有通過者進入共用 scenario cohort。這個 gate 不呼叫 GAI，也不依照 GAI 是否成功重新抽樣。Rule-based 與 GAI 因此共用完全相同的 M4 `scenario_gt`、M5 residual／observation、M7 human gold-standard 與 M8 aggregation。

M6 讀取被選定的 rule bundle。Rule-based 使用既有 capacity-aware multi-source planner；GAI 使用 canonical action adapter。GAI 不使用 Rule-based fallback、不修補 action，也不使用 M7 feedback。若 GAI 已執行但輸出為 `invalid_output` 或 `decision_infeasible`，M7 會建立 `valid=0` 的 failure record，M8 會納入 denominator；只有 provider／budget／transport 沒有 terminal outcome 時，該 row 才是 `availability=unavailable` 且 metrics 為 `null`。

M7 完全獨立，永遠執行：

```text
V(action, human_manual_v1 topology/rules, M4 scenario_gt)
```

因此 AI rule source 產生的非人工 edge、出口或 action 會由人工 gold-standard validator 揭露為正式 violation；AI topology 不得使用自己的規則自我驗證。每筆 M7 record 保存 `decision_rule_source_id`、`decision_topology_checksum`、`validation_rule_source_id=human_manual_v1`、`validation_topology_checksum`、`decision_interface` 與 M4 truth checksum。

M8 仍只讀 M7 trial facts，並以 rule source × interface × framework × regime 分組。每個 paired group 的 `R_ideal`、`R_deploy`、`Delta_R` 只由該 group 的 canonical rows 產生；Paper View 的 `ALL` 只顯示所有 paired rows，不平均、不跨 rule source 合併、不重新計算。UI 每個 paired condition 只顯示一列，固定顯示三個 reliability 欄位，並可選擇顯示 deployment violations、consistency、executed trials 與 availability。若 paired group 的 ideal branch 已失敗，UI 會保留 M8 數值並顯示 interpretation status，不將 zero-headroom 的 `Delta_R` 當成 perception 沒有影響。

AI package 位於 `Data/Topology資料/AI生成`。原始 `/AI生成` map/neighbors 不被修改；`scripts/materialize_ai_topology.py` 以 deterministic 方式產生 `*_rule.json`、nearby-zone 與 manifest，並分開記錄 AI-derived fields 與 system contract fields。這個 rule bundle 的名稱代表「由 AI topology graph materialize」，不代表原始 AI 檔案已自行定義所有 M6 policy。
這些結果描述受正式 Perception residual 影響時的實驗表現，不單獨等同論文的最終結論。

## 14. GAI M6：本地 Ollama canonical action episode

目前 GAI adapter 的正式接入目標是本機 Ollama：

```text
GAI_EXECUTION_MODE=live
LIVE_GAI_PROVIDER_ENABLED=true
GAI_PROVIDER_NAME=ollama
GAI_PROVIDER_MODEL=mistral:7b-instruct-v0.3-q4_K_M
GAI_PROMPT_TEMPLATE_VERSION=m6_ollama_action_v1
```

M6 不把預先產生的 plans、候選 action 清單或選擇題交給模型。系統依當下 branch-visible `decision_population` 建立 action step context，只列出合法 target 與可驗證的 `max_count`；Mistral 必須直接回傳一筆：

```json
{"action_id":"A-0001","from_node":"11","to_node":"8","count":600}
```

`count` 必須等於所選 target 的 `max_count`。系統不修補 count、不換 target、不使用 Rule-based fallback，也不以 M7 feedback 要求模型重答。每筆成功 action 後才更新 shared target capacity，直到 episode 完成，再把完整 actions 交給獨立 M7。

M7 仍固定使用人工 gold-standard topology/rules 與 M4 `scenario_gt`；Mistral 只負責 M6 decision interface。M6 context 不包含 `scenario_gt` truth label、M7 result 或 M8 metrics。每個 action step 的 request/context/response checksum、model、prompt version、retry、latency 與 contract validation 都保存於 `M6/gai_decision_trace.jsonl`。

### 14.1 結果與失敗規則

GAI action episode 狀態分為 `parsed`、`no_action_required`、`invalid_output`、`decision_infeasible`、`timeout`、`error` 與 `unavailable`。模型語意或 contract 錯誤不會被轉成成功 action；transport timeout 可依設定重送一次。`invalid_output` 與 `decision_infeasible` 是已執行的 terminal model outcomes，M7 會建立 `valid=0` record，並由 M8 納入 denominator；`timeout`、`error`、`unavailable` 若沒有 terminal decision，paired metrics 才維持 unavailable/null，絕不把服務中斷轉成 0。

Run 以背景方式執行，`POST /runs` 回傳 `202 + run_id`，可透過 `GET /runs/{run_id}` 讀取 M0～M9 stage progress，並使用 `/cancel` 或 `/resume`。M8/M9 只在完整必要 episodes 完成後發布，Rule-based 結果不受 GAI provider 失敗影響。

若 provider 尚未通過 preflight，系統仍保留 GAI row 與 lineage，但標記 `unavailable`；這與 GAI 已呼叫但 invalid/failed 不同，也不等於 0。

### 14.2 呼叫預算不是固定「每 trial 兩次」

由於一個 branch/trial 可能有多個 high-risk source，或同一 source 需要依剩餘容量分成多筆 action，因此 GAI 呼叫數以 action step 計算，而不是固定用 `trial × 2 branches`。Read-only preflight 會估算：

```text
planned_action_calls =
sum(ideal action steps + deployment action steps)
```

例如 FCU × CSRNet × LOW × 1 trial 的本次 smoke，ideal 需要 2 steps、deployment 需要 2 steps，所以預算至少要 4；若 budget 只有 2，系統會完成前兩筆後將 deployment 標為 `GAI_BUDGET_EXCEEDED`，不把未完成 branch 當成 0。正式 Run 必須先執行 preflight，將 `GAI_BUDGET_MAX_REQUESTS_PER_RUN` 設為不小於 planned action calls；若不足，preflight 會標記 `FAILED` 並列出 shortfall。
```

### 14.3 OpenAI Responses API 與額度耗盡的部分完成

M6 可在建立 Run 時選擇 `ollama` 或 `openai` provider。OpenAI 使用 Responses API 與 Structured Outputs，模型預設為 `gpt-5-nano-2025-08-07`；模型只接收目前 branch 可見的 `decision_population`、合法 target candidates、action step 與 M6 policy context，不接收 `scenario_gt`、M7 結果或 M8 指標。API key 只存在 API／Worker container 的伺服器端環境變數，不進 frontend、trace、artifact 或 manifest。

Provider preflight 只驗證 API key、model access 與 endpoint 可連線，不會產生 action，也不能保證帳戶仍有剩餘額度。實際 action call 若回傳 `insufficient_quota`、billing hard limit 或 account credit exhausted，系統停止後續 OpenAI 呼叫，保留已完成的 M6 action、M7 validation、journal 與 trace，並將 Run 標記為 `PARTIAL_QUOTA_EXHAUSTED`。未完成的 trial 不會被填成 `valid=0`。

Partial Run 的 M8 paired aggregation 只使用同一個 `topology × model × regime × rule source × decision interface × trial` 中，ideal 與 deployment 兩側都完成的 trial。`R_ideal`、`R_deploy`、`Delta_R` 都只對這批 completed pairs 計算；沒有完整 pair 時顯示 `null`／`incomplete paired data`。已執行但輸出不合法的 GAI action 仍是模型結果，依既有規則計為 `valid=0`；額度耗盡、provider unavailable、timeout 或尚未執行則不當成模型失敗。

Partial artifacts 會保存 `partial_publication.json`，包含完成／預期 calls、完成／預期 paired trials、quota error code、第一次額度失敗時間與 `resume_available`。使用相同 Run ID 呼叫 resume 時，系統沿用 frozen config、scenario、observation、seed、prompt version 與 action journal，已完成 action step 不重跑；額度恢復後可繼續產生剩餘結果，成功後 Run 轉為 `SUCCEEDED`。Paper View 可額外勾選 `Run Status`、`Expected Trials`、`Paired Completed Trials` 與 `Completion`，將部分結果與未執行資料清楚分開。

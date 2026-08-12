# Decoupled 2-Stage Experiment Storage

本目錄保存 `decoupled_2_stage_experiment_v1` 的執行輸出。

預期結構：

```text
storage/published/runs/<run_id>/
  M0/
  M1/
  M2/
  M3/
  M4/
  M5/
  M6/
  M7/
  M8/
  M9/
```

在 feasibility-constrained scenario policy 下，M4 會額外保存：

```text
M4/scenario_generation_diagnostics.json
M4/scenario_feasibility_report.json
```

diagnostics 記錄每個 topology × regime 的 accepted scenarios、candidate
attempts、rejection count 與 rejection reasons；拒絕 candidate 不屬於正式
experiment sample。M9 的 delivery/reproducibility manifest 會保存這個檔案的
checksum。

正式輸入資料只允許來自 repository 根目錄的 `Data/`。

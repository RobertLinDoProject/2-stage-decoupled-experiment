import DownloadIcon from "@mui/icons-material/Download";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import HistoryIcon from "@mui/icons-material/History";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RefreshIcon from "@mui/icons-material/Refresh";
import ScienceIcon from "@mui/icons-material/Science";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  LinearProgress,
  ListItemText,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography
} from "@mui/material";
import { useEffect, useMemo, useRef, useState } from "react";

type Topology = { id: string; name: string };
type Model = { model_id: string; model_name: string; paradigm: string };
type Artifact = { path: string; bytes: number; checksum: string };
type GaiRuntime = {
  status?: string;
  enabled?: boolean;
  execution_mode?: string;
  external_calls_allowed?: boolean;
  provider?: string;
  model?: string;
  model_version?: string;
  prompt_template_version?: string;
  temperature?: number | null;
  timeout_ms?: number;
  max_retries?: number;
  budget_max_requests_per_run?: number;
  max_output_tokens?: number | null;
  num_ctx?: number;
  keep_alive?: string;
  seed?: number;
  network_scope?: string;
  request_count?: number;
  parsed_request_count?: number;
  failed_request_count?: number;
  trace_status_counts?: Record<string, number>;
  external_call_count?: number;
  reserved_unavailable_count?: number;
};
type BoundarySelection = {
  ruleSourceId: string;
  topologyId: string;
  modelId: string;
  regime: string;
  decisionInterface: string;
};
type BoundaryPayload = {
  schema_version: string;
  analysis_type: string;
  analysis_mode?: "EXISTING_RUN_ANALYSIS" | "BOUNDARY_SWEEP" | string;
  analysis_label?: string;
  formal_artifacts_modified: boolean;
  context: {
    run_id: string;
    topology_id: string;
    model_id: string;
    regime: string;
    rule_source_id: string;
    decision_interface: string;
    validation_rule_source_id?: string;
    risk_threshold?: number;
    decision_policy_version?: string;
    m7_truth_source?: string;
  };
  analytical_boundary?: {
    status?: string;
    risk_rule?: string;
    note?: string;
    sources?: Array<{
      source_id: string;
      ground_truth_population: number;
      capacity: number;
      first_high_risk_count: number;
      last_non_high_count: number;
      signed_error_boundary: number;
      requested_move_transition_points?: Array<{ observed_population: number; requested_move_count: number }>;
    }>;
  };
  empirical_boundary?: {
    status?: string;
    unavailable_reason?: string;
    actual_alpha?: number;
    max_alpha_for_positive_r?: number | null;
    required_residual_reduction_for_positive_r?: number | null;
    evaluated_transition_count?: number;
    target_thresholds?: Array<{
      target: string;
      max_alpha: number | null;
      required_residual_reduction: number | null;
      status: string;
    }>;
    alpha_points?: Array<{
      alpha: number;
      label?: string;
      valid_trial_count: number;
      executed_trial_count: number;
      r_deploy: number | null;
      violation_reason_counts?: Record<string, number>;
    }>;
  };
  observed_estimate?: {
    status?: string;
    r_ideal?: number | null;
    r_deploy?: number | null;
    valid_trial_count?: number;
    executed_trial_count?: number;
    failure_trial_count?: number;
    error_summary?: {
      signed_mean?: number;
      mae?: number;
      rmse?: number;
      std?: number;
      p90_absolute_error?: number;
      max_absolute_error?: number;
      underestimate_rate?: number;
      overestimate_rate?: number;
    };
    observed_error_interval?: {
      max_error_among_successful_trials?: number | null;
      min_error_among_failed_trials?: number | null;
      status?: string;
    };
    violation_reason_counts?: Record<string, number>;
    trial_results?: Array<Record<string, unknown>>;
    interpretation?: string;
  };
  boundary_sweep?: {
    status?: string;
    available?: boolean;
    reason?: string;
    lambda_curve?: Array<{
      lambda: number;
      label?: string;
      valid_trial_count: number;
      executed_trial_count: number;
      r_deploy: number | null;
      violation_trial_count?: number;
      violation_reason_counts?: Record<string, number>;
      error_summary?: {
        signed_mean?: number;
        mae?: number;
        rmse?: number;
        std?: number;
        p90_absolute_error?: number;
        max_absolute_error?: number;
        underestimate_rate?: number;
        overestimate_rate?: number;
      };
      trial_results?: Array<Record<string, unknown>>;
    }>;
    focus_lambda_curve?: Array<{
      lambda: number;
      label?: string;
      valid_trial_count: number;
      executed_trial_count: number;
      r_deploy: number | null;
      violation_trial_count?: number;
      violation_reason_counts?: Record<string, number>;
      error_summary?: {
        mae?: number;
        p90_absolute_error?: number;
        max_absolute_error?: number;
      };
    }>;
    focus?: {
      status?: string;
      first_zero_lambda?: number | null;
      lambda_min?: number;
      lambda_max?: number | null;
      lambda_step?: number;
      display_mode?: string;
      complete_curve_retained?: boolean;
    };
    targets?: Array<{
      target: string;
      threshold?: number;
      critical_lambda?: number | null;
      safe_critical_lambda?: number | null;
      required_error_reduction_pct?: number | null;
      status: string;
    }>;
    monotonicity?: {
      curve_is_monotonic_nonincreasing?: boolean;
      warning_code?: string | null;
      largest_upward_jump?: number;
    };
    source_checksums?: Record<string, string>;
  };
  critical_evidence?: Array<{
    reason_code: string;
    count: number;
    examples?: Array<{
      trial_id?: string;
      scenario_id?: string;
      node_id?: string;
      source_id?: string;
      target_id?: string;
      capacity?: number;
      post_population?: number;
      message?: string;
      original_code?: string;
    }>;
  }>;
  interpretation?: { status?: string; message?: string };
};
type BoundaryCapability = {
  schema_version: string;
  run_id: string;
  source_run_status?: string;
  condition: BoundarySelection & { framework_mode?: string };
  existing_run_analysis: { available: boolean; level?: string; label?: string };
  boundary_sweep: {
    available: boolean;
    supported_interface?: string;
    lambda_min?: number;
    lambda_max?: number;
    lambda_step?: number;
    lambda_points?: number[];
    reuse_scenarios?: boolean;
    reuse_residuals?: boolean;
    reason?: string | null;
  };
  source_checksums?: Record<string, string>;
  formal_artifacts_modified?: boolean;
};
type BoundaryJob = {
  job_id: string;
  source_run_id: string;
  status: string;
  message?: string;
  current_lambda?: number;
  completed_lambda_count?: number;
  total_lambda_count?: number;
  completed_trial_count?: number;
  failure_code?: string;
};
type BoundaryCacheEntry = {
  capability: BoundaryCapability;
  payload: BoundaryPayload;
  job: BoundaryJob | null;
};
type BoundaryEvidenceExample = NonNullable<NonNullable<BoundaryPayload["critical_evidence"]>[number]["examples"]>[number];
type ScenarioGenerationSummary = {
  policy_id?: string;
  policy_version?: string;
  feasibility_constrained_sampling?: boolean;
  feasibility_oracle_version?: string;
  max_candidate_attempts?: number;
  required_scenario_count?: number;
  accepted_scenario_count?: number;
  rejected_candidate_count?: number;
};
type MatrixRow = {
  condition_id: string;
  base_condition_id?: string;
  rule_source_id?: string;
  rule_source_label?: string;
  topology_id: string;
  topology_name: string;
  model_id: string;
  model_name: string;
  paradigm: string;
  ground_truth_regime: string;
  trial_count: number;
  risk_consistency: number;
  action_consistency: number;
  valid_rate: number;
  invalid_output_rate: number;
  rule_violation_rate: number;
  r_ideal: number;
  r_deploy: number;
  delta_r: number;
  risk_f_beta: number;
};
type AggregateMetricRow = {
  condition_id: string;
  base_condition_id?: string;
  topology_id: string;
  topology_name: string;
  model_id: string;
  model_name: string;
  paradigm: string;
  ground_truth_regime: string;
  framework_condition: string;
  trial_type: string;
  decision_interface: string;
  rule_source_id?: string;
  rule_source_label?: string;
  validation_rule_source_id?: string;
  decision_rule_source_id?: string;
  decision_topology_checksum?: string;
  validation_topology_checksum?: string;
  availability: string;
  metric_policy_id: string;
  metric_policy_version: string;
  risk_f_beta: number | null;
  trial_count: number | null;
  executed_trial_count: number | null;
  ideal_executed_trial_count: number | null;
  deployment_executed_trial_count: number | null;
  ideal_valid_trial_count: number | null;
  deployment_valid_trial_count: number | null;
  valid_rate: number | null;
  execution_outcome_status?: "available" | "invalid_output" | "decision_infeasible" | "unavailable" | string;
  run_status?: string;
  expected_trial_count?: number | null;
  paired_completed_trial_count?: number | null;
  completion?: "complete" | "partial" | "incomplete" | string;
  m6_contract_violation_rate?: number | null;
  m6_decision_infeasible_rate?: number | null;
  risk_precision: number | null;
  risk_recall: number | null;
  risk_consistency: number | null;
  legality_score: number | null;
  priority_score: number | null;
  economy_score: number | null;
  action_consistency: number | null;
  invalid_output_rate: number | null;
  rule_violation_rate: number | null;
  capacity_violation_rate: number | null;
  topology_violation_rate: number | null;
  r_ideal: number | null;
  r_deploy: number | null;
  delta_r: number | null;
  ideal_baseline_scope?: string;
  ideal_action_scope?: string;
  ideal_action_source_trial_id?: string | null;
  unavailable_reason?: string;
};
type SelectedConfiguration = {
  conditionId: string;
  topologyId: string;
  modelId: string;
  regime: string;
};
type SelectedComparison = {
  ruleSourceId: string;
  decisionInterface: string;
};
type PairedInterpretationCode =
  | "AVAILABLE"
  | "IDEAL_BASELINE_FAILED"
  | "IDEAL_BASELINE_PARTIAL"
  | "UNAVAILABLE"
  | "CONSISTENCY_ERROR";
type PairedInterpretation = {
  code: PairedInterpretationCode;
  label: string;
  message: string;
};
type PairedReliabilityGroup = {
  id: string;
  ruleSourceId: string;
  ruleSourceLabel: string;
  decisionInterface: string;
  rIdeal: number | null;
  rDeploy: number | null;
  deltaR: number | null;
  idealCount: number | null;
  deploymentCount: number | null;
  availability: string;
  outcomeStatus: string;
  error: string | null;
  interpretation: PairedInterpretation;
};
type PaperPairedRow = {
  pairKey: string;
  topology_id: string;
  topology_name: string;
  model_id: string;
  model_name: string;
  ground_truth_regime: string;
  rule_source_id: string;
  rule_source_label: string;
  decision_interface: string;
  ideal: AggregateMetricRow | null;
  deployment: AggregateMetricRow | null;
  r_ideal: number | null;
  r_deploy: number | null;
  delta_r: number | null;
  availability: string;
  outcomeStatus: string;
  error: string | null;
  interpretation: PairedInterpretation;
};
type PaperColumnGroup = "core" | "risk" | "action" | "failure" | "governance";
type PaperColumnId =
  | "run_status"
  | "expected_trials"
  | "executed_trials"
  | "paired_completed_trials"
  | "completion"
  | "m6_outcome"
  | "availability"
  | "risk_precision"
  | "risk_recall"
  | "risk_consistency"
  | "risk_beta"
  | "legality"
  | "priority"
  | "economy"
  | "action_consistency"
  | "invalid_output"
  | "rule_violation"
  | "capacity_violation"
  | "topology_violation"
  | "metric_policy";
type PaperColumnDefinition = {
  id: PaperColumnId;
  label: string;
  group: PaperColumnGroup;
  render: (row: PaperPairedRow) => string;
};
type BaselineCell = {
  value: number | null;
  executedTrialCount: number | null;
  modelCount: number;
  shared: boolean;
  inconsistent: boolean;
  interpretation: PairedInterpretation;
};
type BaselineRow = {
  topology: Topology;
  cells: BaselineCell[];
};
type RunSummary = {
  run_id: string;
  status: string;
  profile_id: string;
  display_name: string;
  condition_count: number;
  table_count: number;
  config: {
    profile_id?: string;
    run_purpose?: string;
    root_seed?: number;
    trial_count_per_condition?: number;
    scenarios_per_regime?: number;
    risk_f_beta?: number;
    metric_policy_id?: string;
    metric_policy_version?: string;
    scenario_policy_id?: string;
    scenario_policy_version?: string;
    max_scenario_candidate_attempts?: number;
    decision_policy_id?: string;
    decision_policy_version?: string;
    rule_source_ids?: string[];
    decision_interfaces?: string[];
    m7_validation_rule_source_id?: string;
    selected_topology_ids?: string[];
    selected_model_ids?: string[];
    selected_regimes?: string[];
    selected_interfaces?: string[];
    gai_provider?: "ollama" | "openai" | string;
  };
  topologies: Topology[];
  models: Model[];
  matrix: MatrixRow[];
  metrics: AggregateMetricRow[];
  artifacts: Artifact[];
  scenario_generation?: ScenarioGenerationSummary;
  gai?: GaiRuntime;
  rule_source_ids?: string[];
  rule_sources?: Array<{ id: string; label: string }>;
  m7_validation_rule_source_id?: string;
  limitations: string[];
};
type RunHistoryItem = {
  run_id: string;
  status: string;
  created_at?: string;
  stage_id?: string;
  message?: string;
  condition_count?: number;
  table_count?: number;
  config?: RunSummary["config"];
  scenario_generation?: ScenarioGenerationSummary;
  gai?: GaiRuntime;
};
type ExperimentFailurePayload = {
  message: string;
  status?: string;
  run_id?: string;
  stage_id?: string;
  failure_details?: { report?: string; [key: string]: unknown };
};
type BackgroundRunResponse = { run_id: string; status: string; message?: string };
type ApiError = Error & { payload?: ExperimentFailurePayload };
type RunStatusPayload = {
  run_id: string;
  status: string;
  stage_id?: string;
  message?: string;
  updated_at?: string;
  config?: RunSummary["config"];
  failure_details?: { report?: string; [key: string]: unknown };
};
type Metadata = {
  profile_id: string;
  display_name: string;
  topologies: Topology[];
  models: Model[];
  default_config: {
    root_seed: number;
    trial_count_per_condition: number;
    scenarios_per_regime: number;
    split: string;
    risk_f_beta: number;
    risk_threshold: number;
    scenario_alpha: number;
    scenario_beta: number;
    rho: number;
    hotspot_selection: string;
    metric_policy_version: string;
    scenario_policy_id: string;
    scenario_policy_version: string;
    max_scenario_candidate_attempts: number;
    decision_policy_id: string;
    decision_policy_version: string;
  };
  latest_run?: RunSummary | null;
  gai: GaiRuntime & { note: string };
  rule_sources?: Array<{ id: string; label: string; available?: boolean }>;
  comparison_profile?: { profile_id: string; display_name: string };
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1").replace(/\/$/, "");
const regimes = ["LOW", "MEDIUM", "HIGH"];
const allFilter = "ALL";
const metricTolerance = 1e-6;
const HUMAN_RULE_SOURCE = "human_manual_v1";
const AI_RULE_SOURCE = "ai_generated_derived_v1";
const ruleSourceModes = [
  { id: "topology_compare_all", label: "Topology rule（比較全部）", ids: [HUMAN_RULE_SOURCE, AI_RULE_SOURCE] },
  { id: "topology_human", label: "Topology rule（人工）", ids: [HUMAN_RULE_SOURCE] },
  { id: "topology_ai", label: "Topology rule（AI生成）", ids: [AI_RULE_SOURCE] }
] as const;
const metrics = [
  { id: "r_deploy", label: "R_deploy" },
  { id: "delta_r", label: "Delta R" },
  { id: "risk_consistency", label: "Risk Consistency" },
  { id: "action_consistency", label: "Action Consistency" },
  { id: "invalid_output_rate", label: "Invalid Output" },
  { id: "rule_violation_rate", label: "Rule Violation" }
];
const corePaperColumnIds: PaperColumnId[] = ["executed_trials", "m6_outcome", "availability"];
const ACTIVE_RUN_STORAGE_KEY = "decoupled-2-stage-active-run-id";
const ACTIVE_RUN_STATUSES = new Set(["RUNNING", "QUEUED", "PREFLIGHT", "FREEZING_INPUTS", "RUNNING_LAMBDA_SWEEP", "MONITORING_RETRY", "CANCEL_REQUESTED"]);
const TERMINAL_RUN_STATUSES = new Set(["SUCCEEDED", "PARTIAL_QUOTA_EXHAUSTED", "FAILED", "CANCELLED", "INTERRUPTED_RESUMABLE"]);

function isActiveRunStatus(status: string): boolean {
  return ACTIVE_RUN_STATUSES.has(status);
}

function runStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    RUNNING: "執行中",
    QUEUED: "排隊中",
    PREFLIGHT: "執行前檢查",
    FREEZING_INPUTS: "凍結輸入中",
    RUNNING_LAMBDA_SWEEP: "分析執行中",
    MONITORING_RETRY: "暫時無法連線，持續重試監看",
    CANCEL_REQUESTED: "已提出取消，等待目前步驟停止",
    SUCCEEDED: "已完成",
    PARTIAL_QUOTA_EXHAUSTED: "部分完成：OpenAI 額度耗盡，可恢復",
    FAILED: "執行失敗",
    CANCELLED: "已取消",
    INTERRUPTED_RESUMABLE: "執行中斷，可恢復"
  };
  return labels[status] ?? status;
}

function runStatusSeverity(status: string): "info" | "success" | "warning" | "error" {
  if (status === "SUCCEEDED") return "success";
  if (["FAILED", "INTERRUPTED_RESUMABLE"].includes(status)) return "error";
  if (["CANCELLED", "CANCEL_REQUESTED", "MONITORING_RETRY", "PARTIAL_QUOTA_EXHAUSTED"].includes(status)) return "warning";
  return "info";
}
const paperColumnGroups: Array<{ id: PaperColumnGroup; label: string }> = [
  { id: "core", label: "核心結果" },
  { id: "risk", label: "Risk" },
  { id: "action", label: "Action" },
  { id: "failure", label: "Failure" },
  { id: "governance", label: "治理資訊" }
];
const paperColumnDefinitions: PaperColumnDefinition[] = [
  { id: "run_status", label: "Run Status（ideal / deployment）", group: "core", render: (row) => `${runStatusLabel(row.ideal?.run_status ?? "unavailable")} / ${runStatusLabel(row.deployment?.run_status ?? "unavailable")}` },
  { id: "expected_trials", label: "Expected Trials（ideal / deployment）", group: "core", render: (row) => `${formatCount(row.ideal?.expected_trial_count)} / ${formatCount(row.deployment?.expected_trial_count)}` },
  { id: "executed_trials", label: "Executed Trials（ideal / deployment）", group: "core", render: (row) => `${formatCount(row.ideal?.executed_trial_count)} / ${formatCount(row.deployment?.executed_trial_count)}` },
  { id: "paired_completed_trials", label: "Paired Completed Trials（ideal / deployment）", group: "core", render: (row) => `${formatCount(row.ideal?.paired_completed_trial_count)} / ${formatCount(row.deployment?.paired_completed_trial_count)}` },
  { id: "completion", label: "Completion（ideal / deployment）", group: "core", render: (row) => `${row.ideal?.completion ?? "incomplete"} / ${row.deployment?.completion ?? "incomplete"}` },
  { id: "m6_outcome", label: "M6 Outcome（ideal / deployment）", group: "core", render: (row) => `${displayExecutionOutcome(row.ideal?.execution_outcome_status ?? row.ideal?.availability ?? "unavailable")} / ${displayExecutionOutcome(row.deployment?.execution_outcome_status ?? row.deployment?.availability ?? "unavailable")}` },
  { id: "availability", label: "Availability（ideal / deployment）", group: "core", render: (row) => `${displayAvailability(row.ideal?.availability ?? "unavailable")} / ${displayAvailability(row.deployment?.availability ?? "unavailable")}` },
  { id: "risk_precision", label: "Risk Precision（deployment）", group: "risk", render: (row) => formatMetric(row.deployment?.risk_precision ?? null) },
  { id: "risk_recall", label: "Risk Recall（deployment）", group: "risk", render: (row) => formatMetric(row.deployment?.risk_recall ?? null) },
  { id: "risk_consistency", label: "Risk Consistency（deployment）", group: "risk", render: (row) => formatMetric(row.deployment?.risk_consistency ?? null) },
  { id: "risk_beta", label: "Risk β（deployment）", group: "risk", render: (row) => formatMetric(row.deployment?.risk_f_beta ?? null) },
  { id: "legality", label: "Legality（deployment）", group: "action", render: (row) => formatMetric(row.deployment?.legality_score ?? null) },
  { id: "priority", label: "Priority（deployment）", group: "action", render: (row) => formatMetric(row.deployment?.priority_score ?? null) },
  { id: "economy", label: "Economy（deployment）", group: "action", render: (row) => formatMetric(row.deployment?.economy_score ?? null) },
  { id: "action_consistency", label: "Action Consistency（deployment）", group: "action", render: (row) => formatMetric(row.deployment?.action_consistency ?? null) },
  { id: "invalid_output", label: "Invalid Output（deployment）", group: "failure", render: (row) => formatMetric(row.deployment?.invalid_output_rate ?? null) },
  { id: "rule_violation", label: "Rule Violation（deployment）", group: "failure", render: (row) => formatMetric(row.deployment?.rule_violation_rate ?? null) },
  { id: "capacity_violation", label: "Capacity Violation（deployment）", group: "failure", render: (row) => formatMetric(row.deployment?.capacity_violation_rate ?? null) },
  { id: "topology_violation", label: "Topology Violation（deployment）", group: "failure", render: (row) => formatMetric(row.deployment?.topology_violation_rate ?? null) },
  { id: "metric_policy", label: "Metric Policy", group: "governance", render: (row) => row.deployment?.metric_policy_version ?? row.ideal?.metric_policy_version ?? "unavailable" }
];

export function App() {
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [run, setRun] = useState<RunSummary | null>(null);
  const [runProgress, setRunProgress] = useState<RunStatusPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [preflightResult, setPreflightResult] = useState<Record<string, unknown> | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [history, setHistory] = useState<RunHistoryItem[]>([]);
  const [selectedHistoryRunId, setSelectedHistoryRunId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [failureRun, setFailureRun] = useState<ExperimentFailurePayload | null>(null);
  const [rootSeed, setRootSeed] = useState(114);
  const [trialCount, setTrialCount] = useState(30);
  const [scenarioCount, setScenarioCount] = useState(8);
  const [riskFBeta, setRiskFBeta] = useState(2.0);
  const [runPurpose, setRunPurpose] = useState<"development" | "exploratory" | "formal">("exploratory");
  const [ruleSourceMode, setRuleSourceMode] = useState<(typeof ruleSourceModes)[number]["id"]>("topology_human");
  const [selectedTopologyIds, setSelectedTopologyIds] = useState<string[]>([]);
  const [selectedModelIds, setSelectedModelIds] = useState<string[]>([]);
  const [selectedRegimes, setSelectedRegimes] = useState<string[]>([]);
  const [selectedInterfaces, setSelectedInterfaces] = useState<string[]>(["rule_based"]);
  const [gaiProvider, setGaiProvider] = useState<"ollama" | "openai">("ollama");
  const [deploymentRuleSource, setDeploymentRuleSource] = useState(HUMAN_RULE_SOURCE);
  const [deploymentDecisionInterface, setDeploymentDecisionInterface] = useState("rule_based");
  const [idealBaselineDecisionInterface, setIdealBaselineDecisionInterface] = useState("rule_based");
  const [regime, setRegime] = useState("MEDIUM");
  const [metric, setMetric] = useState("r_deploy");
  const [selectedConfiguration, setSelectedConfiguration] = useState<SelectedConfiguration | null>(null);
  const [selectedComparison, setSelectedComparison] = useState<SelectedComparison>({
    ruleSourceId: HUMAN_RULE_SOURCE,
    decisionInterface: "rule_based"
  });
  const [paperTopologyId, setPaperTopologyId] = useState(allFilter);
  const [paperModelId, setPaperModelId] = useState(allFilter);
  const [paperDecisionInterface, setPaperDecisionInterface] = useState(allFilter);
  const [paperRuleSource, setPaperRuleSource] = useState(allFilter);
  const [paperRegime, setPaperRegime] = useState(allFilter);
  const [paperVisibleColumns, setPaperVisibleColumns] = useState<PaperColumnId[]>(corePaperColumnIds);
  const [boundarySelection, setBoundarySelection] = useState<BoundarySelection | null>(null);
  const [boundaryPayload, setBoundaryPayload] = useState<BoundaryPayload | null>(null);
  const [boundaryCapability, setBoundaryCapability] = useState<BoundaryCapability | null>(null);
  const [boundaryJob, setBoundaryJob] = useState<BoundaryJob | null>(null);
  const [boundarySweepStarting, setBoundarySweepStarting] = useState(false);
  const [boundaryLoading, setBoundaryLoading] = useState(false);
  const [boundaryError, setBoundaryError] = useState<string | null>(null);
  const [boundaryDialogOpen, setBoundaryDialogOpen] = useState(false);
  const [boundaryDialogSelection, setBoundaryDialogSelection] = useState<BoundarySelection | null>(null);
  const [boundaryInlineOpen, setBoundaryInlineOpen] = useState(false);
  const boundaryCacheRef = useRef<Map<string, BoundaryCacheEntry>>(new Map());
  const boundaryRequestIdRef = useRef(0);
  const runMonitorIdRef = useRef<string | null>(null);

  useEffect(() => {
    void loadMetadata();
    void loadRunHistory();
  }, []);

  useEffect(() => {
    const activeHistoryRun = history.find((item) => isActiveRunStatus(item.status));
    let persistedRunId: string | null = null;
    try {
      persistedRunId = window.localStorage.getItem(ACTIVE_RUN_STORAGE_KEY);
    } catch {
      persistedRunId = null;
    }
    const persistedActiveRun = persistedRunId
      ? history.find((item) => item.run_id === persistedRunId && isActiveRunStatus(item.status))
      : undefined;
    const candidate = persistedActiveRun?.run_id ?? activeHistoryRun?.run_id;
    if (!candidate || runMonitorIdRef.current) return;

    runMonitorIdRef.current = candidate;
    setLoading(true);
    void (async () => {
      try {
        const payload = await waitForRun(candidate);
        await loadHistoricalRun(payload.run_id);
      } catch (err) {
        const failure = getExperimentFailurePayload(err);
        setFailureRun(failure);
        setError(formatExperimentError(err));
        void loadRunHistory();
      } finally {
        runMonitorIdRef.current = null;
        setLoading(false);
      }
    })();
  }, [history]);

  useEffect(() => {
    boundaryCacheRef.current.clear();
    boundaryRequestIdRef.current += 1;
    setBoundarySelection(null);
    setBoundaryPayload(null);
    setBoundaryCapability(null);
    setBoundaryJob(null);
    setBoundaryError(null);
    setBoundaryInlineOpen(false);
  }, [run?.run_id]);

  useEffect(() => {
    if (!boundaryInlineOpen) return;
    const selection = selectedBoundarySelection();
    if (selection) void loadBoundary(selection);
  }, [
    boundaryInlineOpen,
    run?.run_id,
    selectedConfiguration?.conditionId,
    selectedConfiguration?.regime,
    selectedComparison.ruleSourceId,
    selectedComparison.decisionInterface,
  ]);

  async function loadMetadata() {
    setError(null);
    try {
      const payload = await request<Metadata>("/decoupled-2-stage-experiment/metadata");
      setMetadata(payload);
      setRootSeed(payload.default_config.root_seed);
      setTrialCount(payload.default_config.trial_count_per_condition);
      setScenarioCount(payload.default_config.scenarios_per_regime);
      setRiskFBeta(payload.default_config.risk_f_beta);
      setSelectedTopologyIds(payload.topologies.map((item) => item.id));
      setSelectedModelIds(payload.models.map((item) => item.model_id));
      setSelectedRegimes([...regimes]);
      setSelectedInterfaces(["rule_based"]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "無法讀取 metadata");
    }
  }

  async function loadRunHistory() {
    setHistoryLoading(true);
    try {
      const payload = await request<RunHistoryItem[]>("/decoupled-2-stage-experiment/runs?limit=50");
      setHistory(payload);
    } catch (err) {
      setHistory([]);
      setError(err instanceof Error ? `無法讀取 Run history：${err.message}` : "無法讀取 Run history");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function loadHistoricalRun(runId: string) {
    setSelectedHistoryRunId(runId);
    if (!runId) return;
    if (runMonitorIdRef.current !== runId) setRunProgress(null);
    setHistoryLoading(true);
    setLoading(true);
    setError(null);
    setFailureRun(null);
    setRun(null);
    setSelectedConfiguration(null);
    setPaperTopologyId(allFilter);
    setPaperModelId(allFilter);
    setPaperDecisionInterface(allFilter);
    setPaperRuleSource(allFilter);
    setPaperRegime(allFilter);
    setPaperVisibleColumns([...corePaperColumnIds]);
    setDeploymentRuleSource(HUMAN_RULE_SOURCE);
    setDeploymentDecisionInterface("rule_based");
    setIdealBaselineDecisionInterface("rule_based");
    setSelectedComparison({ ruleSourceId: HUMAN_RULE_SOURCE, decisionInterface: "rule_based" });
    try {
      const payload = await request<RunSummary>(`/decoupled-2-stage-experiment/runs/${encodeURIComponent(runId)}`);
      if (!["SUCCEEDED", "PARTIAL_QUOTA_EXHAUSTED"].includes(payload.status)) {
        throw new Error("這個 Run 沒有可載入的 M9 結果。");
      }
      setRun(payload);
      setGaiProvider(payload.config?.gai_provider === "openai" ? "openai" : "ollama");
      const firstPaperRow = payload.metrics[0];
      setPaperTopologyId(firstPaperRow?.topology_id ?? allFilter);
      setPaperModelId(firstPaperRow?.model_id ?? allFilter);
      setPaperRegime(firstPaperRow?.ground_truth_regime ?? allFilter);
      setRuleSourceMode(payload.rule_source_ids?.length === 1 && payload.rule_source_ids[0] === AI_RULE_SOURCE ? "topology_ai" : payload.rule_source_ids?.includes(AI_RULE_SOURCE) ? "topology_compare_all" : "topology_human");
      const first = payload.matrix.find((row) => row.ground_truth_regime === regime) ?? payload.matrix[0];
      setSelectedConfiguration(first ? {
        conditionId: first.base_condition_id ?? first.condition_id,
        topologyId: first.topology_id,
        modelId: first.model_id,
        regime: first.ground_truth_regime
      } : null);
      setDeploymentRuleSource(HUMAN_RULE_SOURCE);
      const defaultDecisionInterface = getDefaultDecisionInterface(payload.metrics);
      setDeploymentDecisionInterface(defaultDecisionInterface);
      setIdealBaselineDecisionInterface(normalizeBaselineDecisionInterface(defaultDecisionInterface));
      setSelectedComparison({ ruleSourceId: HUMAN_RULE_SOURCE, decisionInterface: defaultDecisionInterface });
    } catch (err) {
      setRun(null);
      setSelectedConfiguration(null);
      setPaperTopologyId(allFilter);
      setPaperModelId(allFilter);
      setPaperRegime(allFilter);
      setError(err instanceof Error ? `無法載入歷史 Run：${err.message}` : "無法載入歷史 Run");
    } finally {
      setHistoryLoading(false);
      setLoading(false);
    }
  }

  function selectedRuleSourceIds(): string[] {
    return ruleSourceModes.find((item) => item.id === ruleSourceMode)?.ids.slice() ?? [HUMAN_RULE_SOURCE];
  }

  function validateRunSelection(): string[] {
    const errors: string[] = [];
    if (selectedTopologyIds.length === 0) errors.push("至少選擇一個 topology。");
    if (selectedModelIds.length === 0) errors.push("至少選擇一個 perception model。");
    if (selectedRegimes.length === 0) errors.push("至少選擇一個 regime。");
    if (selectedInterfaces.length === 0) errors.push("至少選擇一個決策方式。");
    if (runPurpose === "formal" && trialCount < 30) errors.push("formal Run 需要 trials / condition >= 30。");
    return errors;
  }

  async function performPreflight(): Promise<Record<string, any>> {
    const selectionErrors = validateRunSelection();
    if (selectionErrors.length) {
      const message = selectionErrors.join(" ");
      const result = { status: "FAILED", message };
      setPreflightResult(result);
      throw new Error(`${message} Run 未建立。`);
    }
    setPreflightLoading(true);
    let payload: Record<string, any>;
    try {
      payload = await request<Record<string, any>>("/decoupled-2-stage-experiment/preflight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_purpose: runPurpose,
          root_seed: rootSeed,
          split: "test",
          trial_count_per_condition: trialCount,
          scenarios_per_regime: scenarioCount,
          risk_f_beta: riskFBeta,
          rule_source_ids: selectedRuleSourceIds(),
          selected_topology_ids: selectedTopologyIds,
          selected_model_ids: selectedModelIds,
          selected_regimes: selectedRegimes,
          selected_interfaces: selectedInterfaces,
          gai_provider: gaiProvider
        })
      });
    } catch (error) {
      setPreflightLoading(false);
      const message = error instanceof Error ? error.message : "Preflight request failed";
      setPreflightResult({ status: "FAILED", message });
      throw new Error(`${message} Run 未建立。`);
    }
    let result: Record<string, any> = payload;
    if (selectedInterfaces.includes("gai")) {
      let provider: Record<string, any>;
      try {
        provider = await request<Record<string, any>>(`/decoupled-2-stage-experiment/gai/preflight?provider=${encodeURIComponent(gaiProvider)}`);
      } catch (providerError) {
        provider = { status: "FAILED", message: providerError instanceof Error ? providerError.message : "GAI provider preflight failed" };
      }
      result = { ...payload, provider_preflight: provider };
      if (payload.status !== "PASSED" || provider.status !== "PASSED") {
        result.message = provider.status !== "PASSED"
          ? `GAI provider preflight 未通過：${provider.message ?? "Selected GAI provider is unavailable"}`
          : payload.message;
        setPreflightResult(result);
        setPreflightLoading(false);
        throw new Error(`${String(result.message ?? "GAI preflight 未通過")} Run 未建立。`);
      }
    }
    setPreflightResult(result);
    setPreflightLoading(false);
    return result;
  }

  async function submitRun() {
    setLoading(true);
    setError(null);
    setFailureRun(null);
    setRunProgress(null);
    try {
      const preflight = await performPreflight();
      if (preflight.status !== "PASSED") {
        throw new Error(String(preflight.message ?? "執行前檢查未通過，Run 未建立。"));
      }
      const launch = await request<BackgroundRunResponse>("/decoupled-2-stage-experiment/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_purpose: runPurpose,
          root_seed: rootSeed,
          split: "test",
          trial_count_per_condition: trialCount,
          scenarios_per_regime: scenarioCount,
          risk_f_beta: riskFBeta,
          rule_source_ids: selectedRuleSourceIds(),
          selected_topology_ids: selectedTopologyIds,
          selected_model_ids: selectedModelIds,
          selected_regimes: selectedRegimes,
          selected_interfaces: selectedInterfaces,
          gai_provider: gaiProvider
        })
      });
      setSelectedHistoryRunId(launch.run_id);
      setRunProgress({ run_id: launch.run_id, status: launch.status, message: launch.message });
      runMonitorIdRef.current = launch.run_id;
      try {
        window.localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, launch.run_id);
      } catch {
        // Monitoring still works in-memory when browser storage is unavailable.
      }
      const payload = await waitForRun(launch.run_id);
      setRun(payload);
      const firstPaperRow = payload.metrics[0];
      setPaperTopologyId(firstPaperRow?.topology_id ?? allFilter);
      setPaperModelId(firstPaperRow?.model_id ?? allFilter);
      setPaperRegime(firstPaperRow?.ground_truth_regime ?? allFilter);
      setPaperDecisionInterface(allFilter);
      setPaperRuleSource(allFilter);
      setPaperVisibleColumns([...corePaperColumnIds]);
      setRuleSourceMode(payload.rule_source_ids?.length === 1 && payload.rule_source_ids[0] === AI_RULE_SOURCE ? "topology_ai" : payload.rule_source_ids?.includes(AI_RULE_SOURCE) ? "topology_compare_all" : "topology_human");
      setSelectedHistoryRunId(payload.run_id);
      setFailureRun(null);
      await loadRunHistory();
      const first = payload.matrix.find((row) => row.ground_truth_regime === regime) ?? payload.matrix[0];
      setSelectedConfiguration(first ? {
        conditionId: first.base_condition_id ?? first.condition_id,
        topologyId: first.topology_id,
        modelId: first.model_id,
        regime: first.ground_truth_regime
      } : null);
      setDeploymentRuleSource(HUMAN_RULE_SOURCE);
      const defaultDecisionInterface = getDefaultDecisionInterface(payload.metrics);
      setDeploymentDecisionInterface(defaultDecisionInterface);
      setIdealBaselineDecisionInterface(normalizeBaselineDecisionInterface(defaultDecisionInterface));
      setSelectedComparison({ ruleSourceId: HUMAN_RULE_SOURCE, decisionInterface: defaultDecisionInterface });
    } catch (err) {
      setRun(null);
      setSelectedConfiguration(null);
      setPaperTopologyId(allFilter);
      setPaperModelId(allFilter);
      setPaperRegime(allFilter);
      setDeploymentRuleSource(HUMAN_RULE_SOURCE);
      setDeploymentDecisionInterface("rule_based");
      setIdealBaselineDecisionInterface("rule_based");
      setSelectedComparison({ ruleSourceId: HUMAN_RULE_SOURCE, decisionInterface: "rule_based" });
      const payload = getExperimentFailurePayload(err);
      setFailureRun(payload);
      setError(formatExperimentError(err));
    } finally {
      runMonitorIdRef.current = null;
      setLoading(false);
    }
  }

  async function waitForRun(runId: string): Promise<RunSummary> {
    for (;;) {
      let current: RunStatusPayload;
      try {
        current = await request<RunStatusPayload>(
          `/decoupled-2-stage-experiment/runs/${encodeURIComponent(runId)}`
        );
      } catch (err) {
        setRunProgress((previous) => ({
          run_id: runId,
          status: "MONITORING_RETRY",
          stage_id: previous?.stage_id,
          message: `無法取得最新執行狀態，將持續重試：${err instanceof Error ? err.message : "API request failed"}`,
          updated_at: previous?.updated_at
        }));
        await new Promise((resolve) => window.setTimeout(resolve, 2500));
        continue;
      }

      setRunProgress(current);
      if (current.status === "SUCCEEDED" || current.status === "PARTIAL_QUOTA_EXHAUSTED") {
        try {
          window.localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
        } catch {
          // Ignore storage failures; the terminal state remains visible in UI.
        }
        return current as RunSummary;
      }
      if (TERMINAL_RUN_STATUSES.has(current.status)) {
        try {
          window.localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
        } catch {
          // Ignore storage failures; the terminal state remains visible in UI.
        }
        const error = new Error(current.message ?? `Run ${current.status}`) as ApiError;
        error.payload = {
          message: current.message ?? `Run ${current.status}`,
          status: current.status,
          run_id: runId,
          stage_id: current.stage_id,
          failure_details: current.failure_details
        };
        throw error;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
  }

  async function cancelActiveRun() {
    if (!runProgress) return;
    try {
      await request(`/decoupled-2-stage-experiment/runs/${encodeURIComponent(runProgress.run_id)}/cancel`, { method: "POST" });
      setRunProgress((current) => current ? { ...current, status: "CANCEL_REQUESTED", message: "取消要求已送出，等待目前 action step 結束。" } : current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "無法取消 Run");
    }
  }

  async function resumeRun(runId: string) {
    setLoading(true);
    setError(null);
    try {
      const launch = await request<BackgroundRunResponse>(`/decoupled-2-stage-experiment/runs/${encodeURIComponent(runId)}/resume`, { method: "POST" });
      setRunProgress({ run_id: launch.run_id, status: launch.status, message: launch.message });
      runMonitorIdRef.current = launch.run_id;
      try {
        window.localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, launch.run_id);
      } catch {
        // Monitoring still works in-memory when browser storage is unavailable.
      }
      const payload = await waitForRun(runId);
      setRun(payload);
      const firstPaperRow = payload.metrics[0];
      setPaperTopologyId(firstPaperRow?.topology_id ?? allFilter);
      setPaperModelId(firstPaperRow?.model_id ?? allFilter);
      setPaperRegime(firstPaperRow?.ground_truth_regime ?? allFilter);
      setPaperDecisionInterface(allFilter);
      setPaperRuleSource(allFilter);
      setPaperVisibleColumns([...corePaperColumnIds]);
      setFailureRun(null);
      const defaultDecisionInterface = getDefaultDecisionInterface(payload.metrics);
      setDeploymentDecisionInterface(defaultDecisionInterface);
      setSelectedComparison((current) => ({ ...current, decisionInterface: defaultDecisionInterface }));
      await loadRunHistory();
    } catch (err) {
      setRun(null);
      setFailureRun(getExperimentFailurePayload(err));
      setError(formatExperimentError(err));
    } finally {
      runMonitorIdRef.current = null;
      setLoading(false);
    }
  }

  const topologies = run?.topologies ?? metadata?.topologies ?? [];
  const models = run?.models ?? metadata?.models ?? [];
  const completedHistory = useMemo(
    () => history.filter((item) => ["SUCCEEDED", "PARTIAL_QUOTA_EXHAUSTED"].includes(item.status)),
    [history]
  );
  const historyAttentionRuns = useMemo(
    () => history.filter((item) => item.status !== "SUCCEEDED").slice(0, 5),
    [history]
  );
  const selectedTopologyCount = run?.config.selected_topology_ids?.length ?? (selectedTopologyIds.length || topologies.length);
  const selectedModelCount = run?.config.selected_model_ids?.length ?? (selectedModelIds.length || models.length);
  const selectedRegimeCount = run?.config.selected_regimes?.length ?? (selectedRegimes.length || regimes.length);
  const configurationCount = selectedTopologyCount * selectedModelCount;
  const displayedTrialCount = run?.config.trial_count_per_condition ?? trialCount;
  const displayedRiskFBeta = run?.config.risk_f_beta ?? riskFBeta;
  const pairedTrialCount = configurationCount * selectedRegimeCount * displayedTrialCount;
  const decisionValidationCount = pairedTrialCount * 2;
  const isRuleSourceComparison = run?.profile_id === "decoupled_2_stage_rule_source_comparison_v1";
  const selectedRows = useMemo(() => {
    if (!run || !selectedConfiguration) return [];
    return run.metrics.filter((row) => (
      row.base_condition_id === selectedConfiguration.conditionId
      || row.condition_id === selectedConfiguration.conditionId
    ));
  }, [run, selectedConfiguration]);
  const selectedDeploymentRow = useMemo(() => selectedRows.find((row) => (
    row.ground_truth_regime === selectedConfiguration?.regime
    && row.framework_condition === "w/ Two-stage framework"
    && (row.rule_source_id ?? HUMAN_RULE_SOURCE) === selectedComparison.ruleSourceId
    && row.decision_interface === selectedComparison.decisionInterface
    && row.trial_type === "deployment"
  )), [selectedComparison, selectedConfiguration, selectedRows]);
  const selectedIdealRow = useMemo(() => selectedRows.find((row) => (
    row.ground_truth_regime === selectedConfiguration?.regime
    && row.framework_condition === "w/o Two-stage framework"
    && (row.rule_source_id ?? HUMAN_RULE_SOURCE) === selectedComparison.ruleSourceId
    && row.decision_interface === selectedComparison.decisionInterface
    && row.trial_type === "ideal"
  )), [selectedComparison, selectedConfiguration, selectedRows]);
  const pairedReliabilityGroups = useMemo(
    () => buildPairedReliabilityGroups(selectedRows, selectedConfiguration?.regime),
    [selectedConfiguration?.regime, selectedRows]
  );
  const selectedPairedReliability = useMemo(() => pairedReliabilityGroups.find((group) => (
    group.ruleSourceId === selectedComparison.ruleSourceId
    && group.decisionInterface === selectedComparison.decisionInterface
  )) ?? null, [pairedReliabilityGroups, selectedComparison]);
  const selectedTitle = selectedRows[0]
    ? `${selectedRows[0].topology_name} × ${selectedRows[0].model_name} × ${selectedConfiguration?.regime ?? selectedRows[0].ground_truth_regime}`
    : "尚未選擇設定";
  const paperTopologyOptions = useMemo(
    () => topologies.filter((topology) => (run?.metrics ?? []).some((row) => row.topology_id === topology.id)),
    [run, topologies]
  );
  const paperModelOptions = useMemo(
    () => models.filter((model) => (run?.metrics ?? []).some((row) => (
      (paperTopologyId === allFilter || row.topology_id === paperTopologyId)
      && row.model_id === model.model_id
    ))),
    [models, paperTopologyId, run]
  );
  const paperRegimeOptions = useMemo(() => [...new Set((run?.metrics ?? [])
    .filter((row) => (
      (paperTopologyId === allFilter || row.topology_id === paperTopologyId)
      && (paperModelId === allFilter || row.model_id === paperModelId)
    ))
    .map((row) => row.ground_truth_regime))].sort(), [paperModelId, paperTopologyId, run]);
  const paperRows = useMemo(() => (run?.metrics ?? []).filter((row) => (
    (paperTopologyId === allFilter || row.topology_id === paperTopologyId)
    && (paperModelId === allFilter || row.model_id === paperModelId)
    && (paperRegime === allFilter || row.ground_truth_regime === paperRegime)
  )), [paperModelId, paperRegime, paperTopologyId, run]);
  const paperPairedRows = useMemo(() => buildPaperPairedRows(paperRows), [paperRows]);
  const decisionInterfaceOptions = uniqueRowValues(paperRows, "decision_interface");
  const ruleSourceOptions = uniqueRuleSourceValues(paperRows);
  const filteredPaperPairedRows = useMemo(() => paperPairedRows.filter((row) => (
    (paperRuleSource === allFilter || row.rule_source_id === paperRuleSource)
    && (paperDecisionInterface === allFilter || row.decision_interface === paperDecisionInterface)
  )), [paperDecisionInterface, paperPairedRows, paperRuleSource]);
  const paperHasSingleCondition = paperTopologyId !== allFilter && paperModelId !== allFilter && paperRegime !== allFilter;
  const paperCanOpenPreview = Boolean(
    run
    && paperHasSingleCondition
    && paperRuleSource !== allFilter
    && paperDecisionInterface !== allFilter
  );
  const paperTitle = paperHasSingleCondition
    ? `${paperTopologyOptions.find((item) => item.id === paperTopologyId)?.name ?? paperTopologyId} × ${paperModelOptions.find((item) => item.model_id === paperModelId)?.model_name ?? paperModelId} × ${paperRegime}`
    : "多重條件";
  const visiblePaperColumnDefinitions = paperColumnDefinitions.filter((column) => paperVisibleColumns.includes(column.id));
  const paperColumnCount = 7 + visiblePaperColumnDefinitions.length;
  const baselineDecisionInterfaceOptions = [
    { id: "rule_based", label: "用 rule-based 做決策" },
    { id: "gai", label: "用 GAI 做決策" }
  ];
  const humanIdealBaseline = useMemo(() => topologies.map((topology) => ({
    topology,
    cells: regimes.map((baselineRegime) => buildBaselineCell(run?.metrics ?? [], topology.id, baselineRegime, HUMAN_RULE_SOURCE, idealBaselineDecisionInterface))
  })), [idealBaselineDecisionInterface, run, topologies]);
  const aiIdealBaseline = useMemo(() => topologies.map((topology) => ({
    topology,
    cells: regimes.map((baselineRegime) => buildBaselineCell(run?.metrics ?? [], topology.id, baselineRegime, AI_RULE_SOURCE, idealBaselineDecisionInterface))
  })), [idealBaselineDecisionInterface, run, topologies]);
  const baselineDecisionLabel = displayDecisionInterface(idealBaselineDecisionInterface);
  const baselineConsistencyIssues = useMemo(() => [
    ...humanIdealBaseline.flatMap(({ topology, cells }) => cells
      .map((cell, index) => cell.inconsistent ? `人工規則 / ${baselineDecisionLabel} / ${topology.name} / ${regimes[index]}` : null)
      .filter((value): value is string => value !== null)),
    ...aiIdealBaseline.flatMap(({ topology, cells }) => cells
      .map((cell, index) => cell.inconsistent ? `AI 生成規則 / ${baselineDecisionLabel} / ${topology.name} / ${regimes[index]}` : null)
      .filter((value): value is string => value !== null))
  ], [aiIdealBaseline, baselineDecisionLabel, humanIdealBaseline]);
  const baselineFailureWarnings = useMemo(() => [
    ...humanIdealBaseline.flatMap(({ topology, cells }) => cells
      .map((cell, index) => cell.interpretation.code === "IDEAL_BASELINE_FAILED" ? `人工規則 / ${baselineDecisionLabel} / ${topology.name} / ${regimes[index]}` : null)
      .filter((value): value is string => value !== null)),
    ...aiIdealBaseline.flatMap(({ topology, cells }) => cells
      .map((cell, index) => cell.interpretation.code === "IDEAL_BASELINE_FAILED" ? `AI 生成規則 / ${baselineDecisionLabel} / ${topology.name} / ${regimes[index]}` : null)
      .filter((value): value is string => value !== null))
  ], [aiIdealBaseline, baselineDecisionLabel, humanIdealBaseline]);
  const baselinePartialWarnings = useMemo(() => [
    ...humanIdealBaseline.flatMap(({ topology, cells }) => cells
      .map((cell, index) => cell.interpretation.code === "IDEAL_BASELINE_PARTIAL" ? `人工規則 / ${baselineDecisionLabel} / ${topology.name} / ${regimes[index]}` : null)
      .filter((value): value is string => value !== null)),
    ...aiIdealBaseline.flatMap(({ topology, cells }) => cells
      .map((cell, index) => cell.interpretation.code === "IDEAL_BASELINE_PARTIAL" ? `AI 生成規則 / ${baselineDecisionLabel} / ${topology.name} / ${regimes[index]}` : null)
      .filter((value): value is string => value !== null))
  ], [aiIdealBaseline, baselineDecisionLabel, humanIdealBaseline]);
  const deploymentRuleSourceOptions = isRuleSourceComparison
    ? [
      { id: HUMAN_RULE_SOURCE, label: "人工規則" },
      { id: AI_RULE_SOURCE, label: "AI 生成規則" }
    ]
    : [{ id: HUMAN_RULE_SOURCE, label: "人工規則" }];
  const deploymentDecisionInterfaceOptions = useMemo(
    () => uniqueRowValues(run?.metrics ?? [], "decision_interface")
      .sort((left, right) => decisionInterfaceSortOrder(left) - decisionInterfaceSortOrder(right))
      .map((id) => ({ id, label: displayDecisionInterface(id) })),
    [run]
  );
  const baselineIssues = baselineConsistencyIssues;
  const metricIssues = useMemo(() => {
    const issues: string[] = [];
    if (
      selectedDeploymentRow
      && isNumericMetric(selectedDeploymentRow.valid_rate)
      && !sameMetric(selectedDeploymentRow.r_deploy, selectedDeploymentRow.valid_rate)
    ) {
      issues.push("R_deploy 與 deployment valid rate 不一致。");
    }
    if (
      selectedIdealRow
      && isNumericMetric(selectedIdealRow.valid_rate)
      && !sameMetric(selectedIdealRow.r_ideal, selectedIdealRow.valid_rate)
    ) {
      issues.push("R_ideal 與 ideal valid rate 不一致。");
    }
    return issues;
  }, [selectedDeploymentRow, selectedIdealRow]);

  function openSelectedTopologyPreview() {
    if (!run || !selectedConfiguration) return;
    const params = new URLSearchParams({
      run_id: run.run_id,
      rule_source_id: selectedComparison.ruleSourceId,
      topology_id: selectedConfiguration.topologyId,
      model_id: selectedConfiguration.modelId,
      regime: selectedConfiguration.regime,
      decision_interface: selectedComparison.decisionInterface,
    });
    window.open(`/prototypes/topology-flow-violation-preview.html?${params.toString()}`, "_blank", "noopener,noreferrer");
  }

  function openPaperTopologyPreview() {
    if (
      !run
      || !paperHasSingleCondition
      || paperRuleSource === allFilter
      || paperDecisionInterface === allFilter
    ) return;
    const params = new URLSearchParams({
      run_id: run.run_id,
      rule_source_id: paperRuleSource,
      topology_id: paperTopologyId,
      model_id: paperModelId,
      regime: paperRegime,
      decision_interface: paperDecisionInterface,
    });
    window.open(`/prototypes/topology-flow-violation-preview.html?${params.toString()}`, "_blank", "noopener,noreferrer");
  }

  async function loadBoundary(selection: BoundarySelection): Promise<void> {
    if (!run) return;
    const runId = run.run_id;
    const requestId = boundaryRequestIdRef.current + 1;
    boundaryRequestIdRef.current = requestId;
    const cacheKey = boundarySelectionKey(runId, selection);
    setBoundarySelection(selection);
    const cached = boundaryCacheRef.current.get(cacheKey);
    if (cached) {
      setBoundaryCapability(cached.capability);
      setBoundaryPayload(cached.payload);
      setBoundaryJob(cached.job);
      setBoundaryError(null);
      setBoundaryLoading(false);
      return;
    }
    setBoundaryPayload(null);
    setBoundaryCapability(null);
    setBoundaryJob(null);
    setBoundaryError(null);
    setBoundaryLoading(true);
    const params = new URLSearchParams({
      rule_source_id: selection.ruleSourceId,
      topology_id: selection.topologyId,
      model_id: selection.modelId,
      regime: selection.regime,
      decision_interface: selection.decisionInterface,
    });
    try {
      const capability = await request<BoundaryCapability>(
        `/decoupled-2-stage-experiment/runs/${encodeURIComponent(runId)}/boundary-capability?${params.toString()}`
      );
      if (requestId !== boundaryRequestIdRef.current) return;
      setBoundaryCapability(capability);
      const payload = await request<BoundaryPayload>(
        `/decoupled-2-stage-experiment/runs/${encodeURIComponent(runId)}/perception-error-boundary?${params.toString()}`
      );
      if (requestId !== boundaryRequestIdRef.current) return;
      setBoundaryPayload(payload);
      boundaryCacheRef.current.set(cacheKey, { capability, payload, job: null });
    } catch (err) {
      if (requestId !== boundaryRequestIdRef.current) return;
      setBoundaryError(err instanceof Error ? err.message : "Perception Error Boundary 分析失敗");
    } finally {
      if (requestId === boundaryRequestIdRef.current) setBoundaryLoading(false);
    }
  }

  async function startBoundarySweep(): Promise<void> {
    if (!run || !boundarySelection || boundarySweepStarting) return;
    const sweepSelection = boundarySelection;
    setBoundarySweepStarting(true);
    setBoundaryError(null);
    try {
      const created = await request<BoundaryJob>(
        `/decoupled-2-stage-experiment/runs/${encodeURIComponent(run.run_id)}/boundary-analysis`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            rule_source_id: sweepSelection.ruleSourceId,
            topology_id: sweepSelection.topologyId,
            model_id: sweepSelection.modelId,
            regime: sweepSelection.regime,
            decision_interface: sweepSelection.decisionInterface,
          })
        }
      );
      setBoundaryJob(created);
      await pollBoundarySweep(run.run_id, created.job_id, sweepSelection);
    } catch (err) {
      setBoundaryError(err instanceof Error ? err.message : "Boundary Sweep 建立失敗");
    } finally {
      setBoundarySweepStarting(false);
    }
  }

  async function pollBoundarySweep(runId: string, jobId: string, sweepSelection: BoundarySelection): Promise<void> {
    for (;;) {
      const job = await request<BoundaryJob>(
        `/decoupled-2-stage-experiment/boundary-analysis/${encodeURIComponent(jobId)}?run_id=${encodeURIComponent(runId)}`
      );
      setBoundaryJob(job);
      if (job.status === "SUCCEEDED") {
        const summary = await request<BoundaryPayload>(
          `/decoupled-2-stage-experiment/boundary-analysis/${encodeURIComponent(jobId)}/summary?run_id=${encodeURIComponent(runId)}`
        );
        setBoundaryPayload(summary);
        const cached = boundaryCacheRef.current.get(boundarySelectionKey(runId, sweepSelection));
        if (cached) {
          boundaryCacheRef.current.set(boundarySelectionKey(runId, sweepSelection), { ...cached, payload: summary, job });
        }
        return;
      }
      if (["FAILED", "CANCELLED"].includes(job.status)) {
        if (job.status === "FAILED") setBoundaryError(job.message ?? "Boundary Sweep 失敗");
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 900));
    }
  }

  function selectedBoundarySelection(): BoundarySelection | null {
    if (!run || !selectedConfiguration) return null;
    return {
      ruleSourceId: selectedComparison.ruleSourceId,
      topologyId: selectedConfiguration.topologyId,
      modelId: selectedConfiguration.modelId,
      regime: selectedConfiguration.regime,
      decisionInterface: selectedComparison.decisionInterface,
    };
  }

  function paperBoundarySelection(): BoundarySelection | null {
    if (!run || !paperCanOpenPreview) return null;
    return {
      ruleSourceId: paperRuleSource,
      topologyId: paperTopologyId,
      modelId: paperModelId,
      regime: paperRegime,
      decisionInterface: paperDecisionInterface,
    };
  }

  async function openSelectedBoundary() {
    const selection = selectedBoundarySelection();
    if (selection) await loadBoundary(selection);
  }

  async function openSelectedBoundaryDialog() {
    const selection = selectedBoundarySelection();
    if (!selection) return;
    setBoundaryDialogSelection(selection);
    setBoundaryDialogOpen(true);
    await loadBoundary(selection);
  }

  async function openPaperBoundary() {
    const selection = paperBoundarySelection();
    if (!selection) return;
    setBoundaryDialogSelection(selection);
    setBoundaryDialogOpen(true);
    await loadBoundary(selection);
  }

  function boundaryDownloadUrl(format: "json" | "md"): string | null {
    if (!run || !boundarySelection) return null;
    if (boundaryJob?.job_id && boundaryJob.status === "SUCCEEDED") {
      return `${API_BASE}/decoupled-2-stage-experiment/boundary-analysis/${encodeURIComponent(boundaryJob.job_id)}/download?run_id=${encodeURIComponent(run.run_id)}&format=${format}`;
    }
    const params = new URLSearchParams({
      rule_source_id: boundarySelection.ruleSourceId,
      topology_id: boundarySelection.topologyId,
      model_id: boundarySelection.modelId,
      regime: boundarySelection.regime,
      decision_interface: boundarySelection.decisionInterface,
      format,
    });
    return `${API_BASE}/decoupled-2-stage-experiment/runs/${encodeURIComponent(run.run_id)}/perception-error-boundary?${params.toString()}`;
  }

  function boundarySelectionKey(runId: string, selection: BoundarySelection): string {
    return [runId, selection.ruleSourceId, selection.topologyId, selection.modelId, selection.regime, selection.decisionInterface].join("|");
  }

  function changePaperTopology(value: string) {
    setPaperTopologyId(value);
    if (value === allFilter) {
      setPaperModelId(allFilter);
      setPaperRegime(allFilter);
      return;
    }
    const candidates = (run?.metrics ?? []).filter((row) => row.topology_id === value);
    const nextModelId = candidates[0]?.model_id ?? allFilter;
    setPaperModelId(nextModelId);
    setPaperRegime(candidates.find((row) => row.model_id === nextModelId)?.ground_truth_regime ?? allFilter);
  }

  function changePaperModel(value: string) {
    setPaperModelId(value);
    if (value === allFilter) {
      setPaperRegime(allFilter);
      return;
    }
    const candidates = (run?.metrics ?? []).filter((row) => (
      row.model_id === value
      && (paperTopologyId === allFilter || row.topology_id === paperTopologyId)
    ));
    setPaperRegime(candidates[0]?.ground_truth_regime ?? allFilter);
  }

  function togglePaperColumn(columnId: PaperColumnId, checked: boolean) {
    setPaperVisibleColumns((current) => {
      const next = checked
        ? [...new Set([...current, columnId])]
        : current.filter((item) => item !== columnId);
      return next.length > 0 ? next : [...corePaperColumnIds];
    });
  }

  return (
    <Box className="app-shell">
      <Container maxWidth="xl" className="main-content">
        <Stack spacing={3}>
          <Box className="title-band">
            <Stack spacing={1}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                <ScienceIcon color="primary" />
                <Typography variant="h4" component="h1">Decoupled 2-Stage Experiment</Typography>
              </Stack>
              <Typography color="text.secondary">
                選擇實驗條件後執行；完成的 Run 可從 Run history 重新開啟。
              </Typography>
            </Stack>
          </Box>

          <Box className="run-panel">
            <Stack spacing={2}>
              <Box className="run-history-section">
                <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems={{ md: "center" }} justifyContent="space-between">
                  <Stack direction="row" spacing={1} alignItems="center">
                    <HistoryIcon color="action" />
                    <Box>
                      <Typography variant="subtitle1" fontWeight={700}>Run history</Typography>
                      <Typography variant="body2" color="text.secondary">
                        選取已完成的實驗結果，直接查看表格，不會重新執行。
                      </Typography>
                    </Box>
                  </Stack>
                  <IconButton
                    aria-label="重新整理 Run history"
                    title="重新整理 Run history"
                    onClick={() => void loadRunHistory()}
                    disabled={historyLoading || loading}
                    size="small"
                  >
                    <RefreshIcon />
                  </IconButton>
                </Stack>
                <FormControl fullWidth size="small" className="run-history-select" disabled={historyLoading || loading || completedHistory.length === 0}>
                  <InputLabel id="run-history-label">選擇歷史 Run</InputLabel>
                  <Select
                    labelId="run-history-label"
                    label="選擇歷史 Run"
                    value={selectedHistoryRunId}
                    onChange={(event) => void loadHistoricalRun(event.target.value)}
                  >
                    <MenuItem value=""><em>請選擇已完成的 Run</em></MenuItem>
                    {completedHistory.map((item) => (
                      <MenuItem key={item.run_id} value={item.run_id}>
                        <Box className="run-history-option">
                          <strong>{item.run_id}</strong>
                          <span>{formatRunDate(item.created_at)} · seed {item.config?.root_seed ?? "?"} · trials {item.config?.trial_count_per_condition ?? "?"} · scenarios {item.config?.scenarios_per_regime ?? "?"} · GAI {displayGaiStatus(item.gai)}</span>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                {historyLoading && <LinearProgress className="run-history-progress" />}
                {!historyLoading && completedHistory.length === 0 && (
                  <Typography variant="body2" color="text.secondary" className="run-history-empty">
                    尚無可載入的成功 Run；完成一次實驗後，結果會出現在這裡。
                  </Typography>
                )}
                {historyAttentionRuns
                  .filter((item) => !runProgress || runProgress.run_id !== item.run_id)
                  .map((item) => (
                    <Alert key={item.run_id} severity={runStatusSeverity(item.status)} className="run-history-attention">
                      <Stack spacing={0.75}>
                        <Typography variant="body2">
                          <strong>{runStatusLabel(item.status)}</strong> · {item.run_id} · 階段 {item.stage_id ?? "queued"}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {item.message ?? "尚未取得詳細狀態"}
                        </Typography>
                        {["INTERRUPTED_RESUMABLE", "PARTIAL_QUOTA_EXHAUSTED"].includes(item.status) && (
                          <Button
                            variant="outlined"
                            size="small"
                            startIcon={<RefreshIcon />}
                            onClick={() => void resumeRun(item.run_id)}
                            disabled={loading}
                          >
                            Resume 這個 Run
                          </Button>
                        )}
                      </Stack>
                    </Alert>
                  ))}
              </Box>
              <Box className="run-settings-grid">
                <FormControl size="small" sx={{ minWidth: 150 }}>
                  <InputLabel>Run purpose</InputLabel>
                  <Select
                    label="Run purpose"
                    value={runPurpose}
                    onChange={(event) => setRunPurpose(event.target.value as typeof runPurpose)}
                    disabled={loading}
                  >
                    <MenuItem value="development">development</MenuItem>
                    <MenuItem value="exploratory">exploratory</MenuItem>
                    <MenuItem value="formal">formal (30+ trials)</MenuItem>
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 260 }}>
                  <InputLabel>Rule source set</InputLabel>
                  <Select
                    label="Rule source set"
                    value={ruleSourceMode}
                    onChange={(event) => setRuleSourceMode(event.target.value as typeof ruleSourceMode)}
                    disabled={loading}
                  >
                    {ruleSourceModes.map((item) => <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 230 }}>
                  <InputLabel>決策</InputLabel>
                  <Select
                    multiple
                    label="決策"
                    value={selectedInterfaces}
                    onChange={(event) => setSelectedInterfaces(normalizeMultiSelectValue(event.target.value, ["rule_based", "gai"]))}
                    renderValue={(values) => values.length === 2 ? allFilter : values.map(displayDecisionInterface).join(", ")}
                    disabled={loading}
                  >
                    <MenuItem value={allFilter}><Checkbox checked={selectedInterfaces.length === 2} /><ListItemText primary="ALL" /></MenuItem>
                    <MenuItem value="rule_based">
                      <Checkbox checked={selectedInterfaces.includes("rule_based")} />
                      <ListItemText primary="用 rule-based 做決策" />
                    </MenuItem>
                    <MenuItem value="gai">
                      <Checkbox checked={selectedInterfaces.includes("gai")} />
                      <ListItemText
                        primary="用 GAI 做決策"
                        secondary="執行前會自動檢查 provider、model 與 budget"
                      />
                    </MenuItem>
                  </Select>
                </FormControl>
                {selectedInterfaces.includes("gai") && (
                  <FormControl size="small" sx={{ minWidth: 250 }}>
                    <InputLabel>GAI provider</InputLabel>
                    <Select
                      label="GAI provider"
                      value={gaiProvider}
                      onChange={(event) => setGaiProvider(event.target.value as "ollama" | "openai")}
                      disabled={loading}
                    >
                      <MenuItem value="ollama">Local Ollama · Mistral 7B</MenuItem>
                      <MenuItem value="openai">OpenAI · GPT-5 Nano</MenuItem>
                    </Select>
                  </FormControl>
                )}
                <FormControl size="small" sx={{ minWidth: 230 }}>
                  <InputLabel>Topologies</InputLabel>
                  <Select
                    multiple
                    label="Topologies"
                    value={selectedTopologyIds}
                    onChange={(event) => setSelectedTopologyIds(normalizeMultiSelectValue(event.target.value, topologies.map((item) => item.id)))}
                    renderValue={(values) => values.length === topologies.length ? "ALL" : values.join(", ")}
                    disabled={loading}
                  >
                    <MenuItem value={allFilter}><Checkbox checked={selectedTopologyIds.length === topologies.length} /><ListItemText primary="ALL" /></MenuItem>
                    {topologies.map((item) => (
                      <MenuItem key={item.id} value={item.id}>
                        <Checkbox checked={selectedTopologyIds.includes(item.id)} />
                        <ListItemText primary={item.name} />
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 230 }}>
                  <InputLabel>Perception models</InputLabel>
                  <Select
                    multiple
                    label="Perception models"
                    value={selectedModelIds}
                    onChange={(event) => setSelectedModelIds(normalizeMultiSelectValue(event.target.value, models.map((item) => item.model_id)))}
                    renderValue={(values) => values.length === models.length ? "ALL" : values.join(", ")}
                    disabled={loading}
                  >
                    <MenuItem value={allFilter}><Checkbox checked={selectedModelIds.length === models.length} /><ListItemText primary="ALL" /></MenuItem>
                    {models.map((item) => (
                      <MenuItem key={item.model_id} value={item.model_id}>
                        <Checkbox checked={selectedModelIds.includes(item.model_id)} />
                        <ListItemText primary={item.model_name} secondary={item.paradigm} />
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 170 }}>
                  <InputLabel>Regimes</InputLabel>
                  <Select
                    multiple
                    label="Regimes"
                    value={selectedRegimes}
                    onChange={(event) => setSelectedRegimes(normalizeMultiSelectValue(event.target.value, regimes))}
                    renderValue={(values) => values.length === regimes.length ? "ALL" : values.join(", ")}
                    disabled={loading}
                  >
                    <MenuItem value={allFilter}><Checkbox checked={selectedRegimes.length === regimes.length} /><ListItemText primary="ALL" /></MenuItem>
                    {regimes.map((item) => (
                      <MenuItem key={item} value={item}>
                        <Checkbox checked={selectedRegimes.includes(item)} />
                        <ListItemText primary={item} />
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <TextField
                  label="root seed"
                  type="number"
                  value={rootSeed}
                  onChange={(event) => setRootSeed(Number(event.target.value))}
                  size="small"
                  InputProps={{
                    endAdornment: <ParameterHint label="root seed" text="固定情境生成與 residual 抽樣的亂數起點。相同資料、設定與 seed 會重現相同結果；改 seed 是換一組可重現樣本，不是提高模型效能。" />
                  }}
                />
                <TextField
                  label="trials / condition"
                  type="number"
                  value={trialCount}
                  onChange={(event) => setTrialCount(Number(event.target.value))}
                  size="small"
                  inputProps={{ min: 1, max: 500 }}
                  InputProps={{
                    endAdornment: <ParameterHint label="trials / condition" text="每個 topology、model、regime 的 ideal/deployment 配對次數。提高數值可讓平均指標更穩定，但執行時間與記憶體使用量會近似線性增加。" />
                  }}
                />
                <TextField
                  label="scenarios / regime"
                  type="number"
                  value={scenarioCount}
                  onChange={(event) => setScenarioCount(Number(event.target.value))}
                  size="small"
                  inputProps={{ min: 1, max: 300 }}
                  InputProps={{
                    endAdornment: <ParameterHint label="scenarios / regime" text="每個 topology 與密度區間建立的不同 ground-truth 人群分布數。它增加空間情境多樣性；每個 trial 仍會各自抽取 residual。" />
                  }}
                />
                <TextField
                  label="Risk Consistency β"
                  type="number"
                  value={riskFBeta}
                  onChange={(event) => setRiskFBeta(Number(event.target.value))}
                  size="small"
                  inputProps={{ min: 0.01, step: 0.1 }}
                  helperText="β > 1 時較重視 Recall"
                  InputProps={{
                    endAdornment: <ParameterHint label="Risk Consistency β" text="Risk Consistency 使用 F-beta。β 大於 1 時較重視避免漏掉真正高風險來源；β 小於 1 時較重視避免誤報。每次調整都會建立新的 run。" />
                  }}
                />
              </Box>
              <Stack className="run-action-row" direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                <Button variant="contained" size="large" startIcon={<PlayArrowIcon />} onClick={submitRun} disabled={loading}>
                  執行實驗
                </Button>
                <Button variant="outlined" size="large" startIcon={<RefreshIcon />} onClick={loadMetadata} disabled={loading}>
                  重新整理
                </Button>
                {runProgress && isActiveRunStatus(runProgress.status) && <Button variant="outlined" color="warning" size="large" startIcon={<StopCircleIcon />} onClick={() => void cancelActiveRun()} disabled={runProgress.status === "CANCEL_REQUESTED"}>
                  取消背景 Run
                </Button>}
              </Stack>
              {loading && <LinearProgress />}
              {runProgress && (
                <Alert severity={runStatusSeverity(runProgress.status)} className="run-status-alert" aria-live="polite">
                  <Typography variant="body2"><strong>Run 狀態：{runStatusLabel(runProgress.status)}</strong> · 階段 {runProgress.stage_id ?? "queued"}</Typography>
                  <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }} justifyContent="space-between">
                    <Typography variant="body2">
                      {runProgress.message ?? "等待執行"}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">run_id: {runProgress.run_id}</Typography>
                    {runProgress.updated_at && <Typography variant="caption" color="text.secondary">最後更新：{formatRunDate(runProgress.updated_at)}</Typography>}
                  </Stack>
                  {isActiveRunStatus(runProgress.status) && <>
                    <LinearProgress color={runProgress.status === "MONITORING_RETRY" ? "warning" : "primary"} />
                    <Typography variant="caption" color="text.secondary">此頁面會持續監看；即使暫時失去 API 連線，也會自動重試，不代表實驗已停止。</Typography>
                  </>}
                  {runProgress.status === "PARTIAL_QUOTA_EXHAUSTED" && (
                    <Button
                      variant="outlined"
                      size="small"
                      startIcon={<RefreshIcon />}
                      onClick={() => void resumeRun(runProgress.run_id)}
                      disabled={loading}
                    >
                      額度恢復後 Resume 未完成部分
                    </Button>
                  )}
                </Alert>
              )}
              {preflightLoading && <LinearProgress color="secondary" />}
              {preflightResult && (
                <Alert severity={preflightResult.status === "PASSED" && (preflightResult.provider_preflight as { status?: string } | undefined)?.status !== "FAILED" ? "success" : "warning"}>
                  <Typography variant="body2">
                    Preflight: {String(preflightResult.status ?? "UNKNOWN")}
                    {preflightResult.gai && selectedInterfaces.includes("gai") ? ` · estimated calls: ${String((preflightResult.gai as { effective_budget?: number; planned_calls?: number }).planned_calls ?? 0)} · auto budget: ${String((preflightResult.gai as { effective_budget?: number }).effective_budget ?? "unavailable")}` : ""}
                    {preflightResult.provider_preflight ? ` · provider: ${String((preflightResult.provider_preflight as { status?: string }).status ?? "UNKNOWN")}` : ""}
                  </Typography>
                </Alert>
              )}
              {error && (
                <Alert severity="error">
                  <Stack spacing={0.75}>
                    <Typography variant="body2">{error}</Typography>
                    {failureRun?.run_id && failureRun.failure_details?.report && (
                      <Button
                        variant="outlined"
                        size="small"
                        startIcon={<DownloadIcon />}
                        href={`${API_BASE}/decoupled-2-stage-experiment/runs/${failureRun.run_id}/files/${failureRun.failure_details.report}`}
                      >
                        下載 failure diagnostics
                      </Button>
                    )}
                    {failureRun?.run_id && ["FAILED", "CANCELLED", "INTERRUPTED_RESUMABLE", "PARTIAL_QUOTA_EXHAUSTED"].includes(failureRun.status ?? "") && (
                      <Button variant="outlined" size="small" startIcon={<RefreshIcon />} onClick={() => void resumeRun(failureRun.run_id!)} disabled={loading}>
                        以相同設定 Resume
                      </Button>
                    )}
                  </Stack>
                </Alert>
              )}
              {configurationCount > 0 && (
                <Typography className="run-scale" variant="body2" color="text.secondary">
                   本次設定：{configurationCount} 個 topology/model 組合 × {regimes.length} 個密度區間 × {displayedTrialCount} 組配對；{isRuleSourceComparison ? "人工與 AI 規則來源共用 scenario／observation，Paper View 展開八組比較。" : `${pairedTrialCount.toLocaleString()} 組 ideal/deployment 比較。`}
                </Typography>
              )}
              <details className="parameter-guide">
                <summary>實驗參數說明</summary>
                <Stack className="parameter-guide-content" spacing={1.25}>
                  <Typography variant="body2"><strong>root seed：</strong>固定 M4 情境與 M5 residual 抽樣；相同 seed 可重現結果。</Typography>
                  <Typography variant="body2"><strong>trials / condition：</strong>每個 topology × model × regime 的配對重複次數。數值越大，平均結果越穩定，但執行成本越高。</Typography>
                  <Typography variant="body2"><strong>scenarios / regime：</strong>每個 topology × regime 的不同 GT 人群分布數。數值越大，空間情境覆蓋越廣；trial 會循環使用這些 scenarios 並重新抽樣 residual。</Typography>
                  <Typography variant="body2"><strong>Risk Consistency β：</strong>控制 F-beta 對 Precision 與 Recall 的相對重視程度。調整 beta 會建立新 run，不會改寫既有結果。</Typography>
                  <Typography variant="body2"><strong>Rule source set：</strong>可選比較全部、只跑人工規則或只跑 AI 生成規則。AI-only 仍由人工規則提供 M7 gold standard；兩種來源共用 scenario、residual sample 與 observation。</Typography>
                </Stack>
              </details>
            </Stack>
          </Box>

          {run && (
            <Box className="section">
              <Stack className="section-heading" direction="row" spacing={0.25} alignItems="center">
                <Box>
                  <Typography variant="h6">Ideal Baseline by Rule Source</Typography>
                  <Typography className="section-purpose" variant="body2">
                    先確認規則本身的決策能力：假設人數辨識完全正確時，該規則能通過人工 M7 驗證的比例。
                  </Typography>
                </Box>
                <SectionHint label="Ideal Baseline by Rule Source 說明" text="人工規則與 AI 生成規則各自使用正確人數輸入產生 ideal baseline。兩者都由同一套人工 M7 gold-standard validator 驗證，但不可混為同一個 baseline。" />
              </Stack>
              <Box className="section-purpose-note">
                <Typography variant="body2"><strong>怎麼看：</strong>每一格是 `R_ideal` 與實際 trial 數。新 Run 的 ideal decision 由規則來源 × topology × regime × trial 共用，不再因五個 perception model 重複執行；model 只在 deployment branch 影響 observation。</Typography>
              </Box>
              <Box className="baseline-decision-selector">
                <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "center" }}>
                  <Box>
                    <Typography variant="body2" fontWeight={700}>Ideal Baseline 的決策方式</Typography>
                    <Typography variant="caption" color="text.secondary">
                      兩張規則來源矩陣會同步切換；這個選擇不會影響 Deployment Comparison Index、Selected Configuration Detail 或 Paper View。
                    </Typography>
                  </Box>
                  <FormControl size="small" sx={{ minWidth: 220 }}>
                    <InputLabel id="ideal-baseline-decision-label">決策方式</InputLabel>
                    <Select
                      labelId="ideal-baseline-decision-label"
                      value={idealBaselineDecisionInterface}
                      label="決策方式"
                      onChange={(event) => setIdealBaselineDecisionInterface(event.target.value)}
                    >
                      {baselineDecisionInterfaceOptions.map((item) => (
                        <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Stack>
              </Box>
              <details className="result-guide">
                <summary>Ideal Baseline by Rule Source 在看什麼？</summary>
                <Stack className="result-guide-content" spacing={1.25}>
                  <Typography variant="body2"><strong>輸入：</strong>w/o ideal 直接使用 scenario ground truth，不注入 Perception residual。</Typography>
                  <Typography variant="body2"><strong>規則來源與決策方式：</strong>人工規則與 AI 生成規則各自保留；上方「決策方式」會同步切換兩張矩陣的 Rule-based 或 GAI 結果。</Typography>
                  <Typography variant="body2"><strong>目前選擇：</strong>{baselineDecisionLabel}。兩種決策方式都由人工 M7 gold-standard validator 驗證。</Typography>
                  <Typography variant="body2"><strong>注意：</strong>AI baseline 低於 1 代表規則本身已與人工驗證標準有差異，不能直接把後續 Delta R 解讀為 Perception 影響。</Typography>
                  <Typography variant="body2"><strong>GAI 狀態：</strong>若 GAI 沒有 terminal result，顯示 `unavailable`；若已執行但輸出無效或無法完成分配，則保留正式 `R_ideal = 0`，不把兩者混為一談。</Typography>
                  <Typography variant="body2"><strong>新 Run 的範圍：</strong>Ideal baseline 不含 perception model 維度；同一 rule source × topology × regime 只執行一個 ideal decision episode，M8 會在各 model paired row 重複引用這個共同值。</Typography>
                  <Typography variant="body2"><strong>舊 Run：</strong>若是修改前產生的 Run，可能保留每個 model 各自的 GAI ideal 結果。數值不一致時顯示 `Baseline consistency error`，不平均、不改寫歷史資料，請到 Paper View 查看各 model 原始列。</Typography>
                </Stack>
              </details>
              <Box className="baseline-comparison-grid">
                <BaselineMatrix
                  title={`Human Rule Ideal Baseline · ${baselineDecisionLabel}`}
                    description={`人工規則 + ${baselineDecisionLabel} + w/o ideal；新 Run 為不含 perception model 維度的共同 ideal reference。`}
                  rows={humanIdealBaseline}
                  ariaLabel="human rule ideal baseline matrix"
                />
                {isRuleSourceComparison ? (
                  <BaselineMatrix
                    title={`AI-generated Rule Ideal Baseline · ${baselineDecisionLabel}`}
                    description={`AI 生成規則 + ${baselineDecisionLabel} + w/o ideal；新 Run 為不含 perception model 維度的共同 ideal reference，由人工 M7 gold-standard 驗證。`}
                    rows={aiIdealBaseline}
                    ariaLabel="AI-generated rule ideal baseline matrix"
                  />
                ) : (
                  <Box className="baseline-source-panel baseline-source-unavailable">
                    <Typography variant="subtitle1">AI-generated Rule Ideal Baseline</Typography>
                    <Typography variant="body2" color="text.secondary">目前 Run 沒有 AI rule source；請執行人工 + AI 生成規則比較 profile 後查看。</Typography>
                  </Box>
                )}
              </Box>
              {baselineIssues.length > 0 && (
                <Alert className="section-alert" severity="error">
                  Ideal baseline consistency error: {baselineIssues.join("；")} 的 model rows 有不同 R_ideal。這通常表示該 Run 產生於共用 ideal baseline 版本之前；不取平均，請到 Paper View 查看各 model 的原始 paired 結果。
                </Alert>
              )}
              {baselineFailureWarnings.length > 0 && (
                <Alert className="section-alert" severity="warning">
                  Ideal baseline failed：{baselineFailureWarnings.join("；")} 的 R_ideal 為 0，請回到 M7 violation evidence；不要將其 Delta R 解讀為沒有 perception degradation。
                </Alert>
              )}
              {baselinePartialWarnings.length > 0 && (
                <Alert className="section-alert" severity="info">
                  Ideal baseline is partial：{baselinePartialWarnings.join("；")} 的 R_ideal 未達 1，相關 Delta R 只能作為條件性比較。
                </Alert>
              )}
            </Box>
          )}

          <Box className="section">
            <Stack className="deployment-header" direction={{ xs: "column", md: "row" }} spacing={1.5} justifyContent="space-between" alignItems={{ md: "center" }}>
              <Stack className="section-heading" direction="row" spacing={0.25} alignItems="center">
                <Box>
                  <Typography variant="h6">Deployment Comparison Index</Typography>
                  <Typography className="section-purpose" variant="body2">
                    再看含有 Perception 觀測誤差的人數輸入時，所選決策方式在不同模型與場域的部署結果。
                  </Typography>
                </Box>
                <SectionHint label="Deployment Comparison Index 說明" text="這是 deployment 快速選擇入口。可切換人工或 AI 生成規則，也可選擇用 rule-based 或 GAI 做決策；完整配對比較請看 Paper View。" />
              </Stack>
              <Stack className="deployment-filters" direction="row" spacing={1}>
                <FormControl size="small" sx={{ minWidth: 180 }} disabled={loading}>
                  <InputLabel>Rule Source</InputLabel>
                  <Select
                    label="Rule Source"
                    value={deploymentRuleSource}
                    onChange={(event) => {
                      const nextSource = event.target.value;
                      setDeploymentRuleSource(nextSource);
                      setSelectedComparison({ ruleSourceId: nextSource, decisionInterface: deploymentDecisionInterface });
                    }}
                  >
                    {deploymentRuleSourceOptions.map((item) => <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 190 }} disabled={!run || loading || deploymentDecisionInterfaceOptions.length === 0}>
                  <InputLabel>決策</InputLabel>
                  <Select
                    label="決策"
                    value={deploymentDecisionInterface}
                    onChange={(event) => {
                      const nextInterface = event.target.value;
                      setDeploymentDecisionInterface(nextInterface);
                      setSelectedComparison({ ruleSourceId: deploymentRuleSource, decisionInterface: nextInterface });
                    }}
                  >
                    {deploymentDecisionInterfaceOptions.map((item) => <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 150 }}>
                  <InputLabel>Regime</InputLabel>
                  <Select label="Regime" value={regime} onChange={(event) => setRegime(event.target.value)}>
                    {regimes.map((item) => <MenuItem key={item} value={item}>{item}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 190 }}>
                  <InputLabel>Metric</InputLabel>
                  <Select label="Metric" value={metric} onChange={(event) => setMetric(event.target.value)}>
                    {metrics.map((item) => <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
                  </Select>
                </FormControl>
              </Stack>
            </Stack>
            <Box className="section-purpose-note">
              <Typography variant="body2"><strong>這張矩陣只做快速選擇：</strong>固定為 `w/ + deployment + {displayDecisionInterface(deploymentDecisionInterface)}`；點擊一格後，下方會同步顯示同一組條件的完整配對結果。</Typography>
            </Box>
            <details className="result-guide">
              <summary>Deployment Comparison Index 說明</summary>
              <Stack className="result-guide-content" spacing={1.25}>
                <Typography variant="body2"><strong>列與欄：</strong>每列是一個 Perception model，並標示 Detection 或 Density；每欄是一個正式 topology。</Typography>
                <Typography variant="body2"><strong>固定範圍：</strong>每一格是目前 rule source 與所選決策方式的 <code>w/ + deployment</code> 結果，不是八組結果的總結。</Typography>
                <Typography variant="body2"><strong>Rule Source：</strong>切換人工規則與 AI 生成規則；切換後矩陣、Selected Configuration Detail 與目前比較 context 會同步更新。</Typography>
                <Typography variant="body2"><strong>決策：</strong>選擇用 rule-based 做決策或用 GAI 做決策；GAI 若 unavailable，矩陣保留 unavailable，不以 0 代替。</Typography>
                <Typography variant="body2"><strong>Regime：</strong>切換 LOW、MEDIUM、HIGH 的 ground-truth density slice；不會改變已完成 run。</Typography>
                <Typography variant="body2"><strong>Metric：</strong>預設先看 <code>R_deploy</code>；切換到 <code>Delta R</code> 時，請記得它是目前 rule source × model × topology × regime 的 paired 指標：<code>R_ideal - R_deploy</code>。</Typography>
                <Typography variant="body2"><strong>點選格子：</strong>選取該 model × topology × regime，讓下方顯示目前規則來源的 Selected Configuration Detail。Paper View 有自己的 Topology × Model × Regime 選擇，不會被這張矩陣改變。</Typography>
              </Stack>
            </details>
            <TableContainer className="matrix-table">
              <Table size="small" aria-label="decoupled two-stage result matrix">
                <TableHead>
                  <TableRow>
                    <TableCell>Model / Topology</TableCell>
                    {topologies.map((topology) => <TableCell key={topology.id}>{topology.name}</TableCell>)}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {models.map((model) => (
                    <TableRow key={model.model_id}>
                      <TableCell className="row-label">
                        <strong>{model.model_name}</strong>
                        <span>{model.paradigm}</span>
                      </TableCell>
                      {topologies.map((topology) => {
                        const cell = run?.metrics.find((row) => (
                          row.model_id === model.model_id
                          && row.topology_id === topology.id
                          && row.ground_truth_regime === regime
                          && row.framework_condition === "w/ Two-stage framework"
                          && row.trial_type === "deployment"
                          && row.decision_interface === deploymentDecisionInterface
                          && (row.rule_source_id ?? HUMAN_RULE_SOURCE) === deploymentRuleSource
                        ));
                        return (
                          <TableCell key={`${model.model_id}-${topology.id}`}>
                            <Tooltip title={<MatrixCellTooltip row={cell} metric={metric} regime={regime} run={run} />} arrow>
                              <span>
                                <button
                                  className={`metric-cell ${selectedConfiguration?.conditionId === (cell?.base_condition_id ?? cell?.condition_id) && selectedConfiguration?.regime === regime ? "selected" : ""}`}
                                  type="button"
                                  disabled={!cell}
                                  onClick={() => {
                                    if (!cell) return;
                                    setDeploymentDecisionInterface(cell.decision_interface);
                                    setSelectedComparison({ ruleSourceId: deploymentRuleSource, decisionInterface: cell.decision_interface });
                                    setSelectedConfiguration({
                                      conditionId: cell.base_condition_id ?? cell.condition_id,
                                      topologyId: cell.topology_id,
                                      modelId: cell.model_id,
                                      regime: cell.ground_truth_regime
                                    });
                                  }}
                                >
                                  <span className="metric-value">{formatMetric(getMatrixMetric(cell, metric))}</span>
                                  <span className="metric-caption">{cell ? `${regime} · n=${cell.trial_count}` : "unavailable"}</span>
                                </button>
                              </span>
                            </Tooltip>
                          </TableCell>
                        );
                      })}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>

          {!selectedConfiguration && (
            <Box className="section selected-detail selected-detail-empty">
              <Typography variant="h6">Selected Configuration Detail</Typography>
              <Typography className="detail-intro" variant="body2">
                請先在上方 Deployment Comparison Index 點選一個 model × topology × regime，這裡就會顯示該組條件的詳細結果。
              </Typography>
              <Box className="selected-detail-empty-guide">
                <Typography variant="body2"><strong>Reliability Comparison：</strong>比較正確人數輸入的 R_ideal、含 Perception 觀測的人數輸入的 R_deploy，以及兩者的 Delta R。</Typography>
                <Typography variant="body2"><strong>Consistency：</strong>查看 M6 是否找對高風險來源，以及 action 是否符合合法性、優先順序與效率規則。</Typography>
                <Typography variant="body2"><strong>Failure Breakdown：</strong>查看 M6 輸出／分配問題，以及 M7 判斷的規則、容量與拓撲違反。</Typography>
              </Box>
            </Box>
          )}

          {selectedConfiguration && (
            <Box className="section selected-detail">
              <Stack direction={{ xs: "column", md: "row" }} spacing={1} justifyContent="space-between" alignItems={{ md: "center" }}>
                <Box>
                  <Stack direction="row" spacing={0.25} alignItems="center">
                    <Typography variant="h6">Selected Configuration Detail</Typography>
                    <SectionHint label="Selected Configuration Detail 說明" text="這裡將你在 Deployment Comparison Index 矩陣選取的一個 topology × model × regime × rule source × 決策方式條件完整攤開，用來比較該配對的 ideal 與 deployment 可靠度，並看出一致性與失敗原因；不會重新計算任何結果。" />
                  </Stack>
                  <Typography color="text.secondary">
                    {selectedTitle} × {selectedDeploymentRow?.rule_source_label ?? selectedIdealRow?.rule_source_label ?? displayRuleSource(selectedComparison.ruleSourceId)} × {displayDecisionInterface(selectedComparison.decisionInterface)}
                  </Typography>
                  <Typography className="detail-intro" variant="body2">
                    這裡只回答目前這一組 topology、Perception model、density regime、規則來源與決策方式的結果；它用來看「決策是否通過驗證、為什麼失敗」，不代表整個 Run 的平均結果。
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Button variant="outlined" size="small" startIcon={<ScienceIcon />} onClick={() => void openSelectedBoundaryDialog()}>
                    Perception Error Boundary
                  </Button>
                  <Button variant="outlined" size="small" startIcon={<ScienceIcon />} onClick={openSelectedTopologyPreview}>
                    Open topology preview
                  </Button>
                </Stack>
              </Stack>
              <Box className="selected-result-summary">
                <Typography variant="body2" className="selected-result-summary-label">目前選取結果</Typography>
                {selectedPairedReliability ? (
                  <Typography variant="body1">
                    正確人數輸入 <strong>R_ideal = {formatMetric(selectedPairedReliability.rIdeal)}</strong>；含 Perception 觀測 <strong>R_deploy = {formatMetric(selectedPairedReliability.rDeploy)}</strong>；可靠度差異 <strong>Delta R = {formatMetric(selectedPairedReliability.deltaR)}</strong>。
                  </Typography>
                ) : (
                  <Typography variant="body1">目前尚未取得這組條件的完整 ideal / deployment 配對結果。</Typography>
                )}
              </Box>
              <details className="result-guide">
                <summary>Selected Configuration Detail 在看什麼？</summary>
                <Stack className="result-guide-content" spacing={1.25}>
                  <Typography variant="body2">這裡只解讀目前選取的 topology × model × regime × 規則來源 × 決策方式，不會重新執行或計算結果。</Typography>
                  <Typography variant="body2"><strong>Reliability Comparison：</strong>先看同一批 scenario/trial 在兩種人數輸入下是否通過 M7；<code>R_ideal</code> 使用正確的 scenario_gt，<code>R_deploy</code> 使用含 Perception 觀測誤差的人數，<code>Delta R</code> 是兩者的差距。</Typography>
                  <Typography variant="body2"><strong>Consistency：</strong>再看 M6 是否找對高風險來源，以及產生的 action 是否符合優先順序與效率規則；這些是診斷指標，不會重新定義 <code>R_deploy</code>。</Typography>
                  <Typography variant="body2"><strong>Failure Breakdown：</strong>最後看沒有通過時是哪一層出問題：M6 輸出、M6 可行性、M7 規則、容量或拓撲。</Typography>
                  <Typography variant="body2"><strong>資料來源：</strong>數值直接來自 M8 canonical rows；單一 trial 的人流與 M7 evidence 請開啟 topology preview。</Typography>
                </Stack>
              </details>
              {metricIssues.length > 0 && (
                <Alert className="section-alert" severity="error">
                  Metric consistency error: {metricIssues.join(" ")}請檢查 M7 trial facts、M8 aggregation 或 metric policy version。
                </Alert>
              )}
              {selectedPairedReliability?.error && (
                <Alert className="section-alert" severity="error">
                  Paired reliability consistency error：{selectedPairedReliability.error}
                </Alert>
              )}
              {selectedPairedReliability?.interpretation.code === "IDEAL_BASELINE_FAILED" && (
                <Alert className="section-alert" severity="warning">
                  <strong>Ideal baseline failed — Delta R has no interpretable headroom</strong><br />
                  {selectedPairedReliability.interpretation.message} 這表示該 rule source 的 ideal branch 已失敗，因此不能用 Delta R 判斷 perception residual 的額外影響；請回到 M7 violation evidence 查看原因。
                </Alert>
              )}
              {selectedPairedReliability?.interpretation.code === "IDEAL_BASELINE_PARTIAL" && (
                <Alert className="section-alert" severity="warning">
                  <strong>Ideal baseline is partial</strong><br />
                  R_ideal 未達 1.000；目前 Delta R 只能解讀為不完整 ideal baseline 上的條件性比較，不代表完整理想決策能力。
                </Alert>
              )}
              {selectedComparison.decisionInterface === "gai" && run?.gai && (
                <Alert className="section-alert" severity={run.gai.status === "configured" ? "info" : "warning"}>
                  GAI status={displayGaiStatus(run.gai)}。M6 會以本地 Ollama 逐步產生 canonical action；每筆 action 都由 M6 contract 驗證，再交給獨立 M7。provider unavailable 不會填 0；已執行的 invalid_output／decision_infeasible 會以 valid=0 納入 M8，且不會以 Rule-based 結果替代。
                  {run.artifacts.some((artifact) => artifact.path === "M6/gai_decision_trace.jsonl") && (
                    <Button
                      size="small"
                      startIcon={<DownloadIcon />}
                      href={`${API_BASE}/decoupled-2-stage-experiment/runs/${run.run_id}/files/M6/gai_decision_trace.jsonl`}
                    >
                      下載 GAI trace
                    </Button>
                  )}
                </Alert>
              )}
              {!selectedDeploymentRow || !selectedIdealRow ? (
                <Alert className="section-alert" severity="warning">找不到所選 {displayRuleSource(selectedComparison.ruleSourceId)} + {displayDecisionInterface(selectedComparison.decisionInterface)} 的完整 ideal/deployment 配對資料。</Alert>
              ) : selectedDeploymentRow.availability === "unavailable" || selectedIdealRow.availability === "unavailable" ? (
                <Alert className="section-alert" severity="info">
                  {displayRuleSource(selectedComparison.ruleSourceId)} + {displayDecisionInterface(selectedComparison.decisionInterface)} 目前為 unavailable；這個介面尚未產生正式 M7/M8 metrics，不以 0 代替。
                </Alert>
              ) : (
                <Stack className="detail-layout" spacing={2}>
                  {(selectedIdealRow.execution_outcome_status === "invalid_output"
                    || selectedDeploymentRow.execution_outcome_status === "invalid_output"
                    || selectedIdealRow.execution_outcome_status === "decision_infeasible"
                    || selectedDeploymentRow.execution_outcome_status === "decision_infeasible") && (
                    <Alert className="section-alert" severity="warning">
                      M6 terminal outcome：ideal={displayExecutionOutcome(selectedIdealRow.execution_outcome_status)}；deployment={displayExecutionOutcome(selectedDeploymentRow.execution_outcome_status)}。這是已執行介面的結果，會以 valid=0 納入 M8 denominator，不是 provider unavailable。
                    </Alert>
                  )}
                  <Box className="detail-group">
                    <Typography variant="subtitle2">Reliability Comparison</Typography>
                    <Typography className="detail-group-explanation" variant="body2">
                      這一段回答「決策通過 M7 的比例有沒有因為 Perception 觀測而下降」。兩個分支使用同一組 scenario/trial；只比較 M6 看到的人數輸入不同所造成的結果。
                    </Typography>
                    {selectedIdealRow.ideal_baseline_scope?.includes("model_id excluded") && (
                      <Typography className="detail-group-explanation" variant="body2">
                        本次 <code>R_ideal</code> 是同一個 rule source × topology × regime × trial 的共用 ideal reference；Perception model 只影響 deployment branch 的 observation，不會另外產生五個 ideal baseline。
                      </Typography>
                    )}
                    <Box className="detail-metric-grid">
                      <DetailMetric label="R_ideal" value={selectedIdealRow.r_ideal} note={`w/o · scenario_gt · M7 valid rate · n=${formatCount(selectedIdealRow.executed_trial_count)}`} />
                      <DetailMetric label="R_deploy" value={selectedDeploymentRow.r_deploy} note={`w/ · observed_population · M7 valid rate · n=${formatCount(selectedDeploymentRow.executed_trial_count)}`} />
                      <DetailMetric label="Delta R" value={selectedDeploymentRow.delta_r} note="R_ideal − R_deploy；越大表示 deployment 相對理想輸入的可靠度落差越大。" />
                    </Box>
                  </Box>
                  <details className="detail-more">
                    <summary>查看一致性與失敗細節</summary>
                    <Stack spacing={2} className="detail-more-content">
                      <Box className="detail-group">
                        <Typography variant="subtitle2">Consistency</Typography>
                        <Typography className="detail-group-explanation" variant="body2">
                          這一段回答「M6 是否找對該疏散的來源，以及 action 是否符合決策規則」。<strong>Risk Precision</strong> 是 M6 標為高風險的來源中真正高風險的比例；<strong>Risk Recall</strong> 是真正高風險來源中被 M6 找到的比例；<strong>β</strong> 是 Precision 與 Recall 的權重設定。<strong>Legality</strong> 看 action 是否合法，<strong>Priority</strong> 看是否依規則選擇較優先的目標，<strong>Economy</strong> 看是否避免不必要的分流。它們是 M6 品質的拆解，不能直接當成 M7 通過率。
                        </Typography>
                        <Box className="detail-metric-grid">
                          <DetailMetric label="Risk Consistency" value={selectedDeploymentRow.risk_consistency} note={`真正高風險來源的命中綜合分數；Precision ${formatMetric(selectedDeploymentRow.risk_precision)} · Recall ${formatMetric(selectedDeploymentRow.risk_recall)} · β=${formatMetric(selectedDeploymentRow.risk_f_beta)}`} />
                          <DetailMetric label="Action Consistency" value={selectedDeploymentRow.action_consistency} note={`action 品質綜合分數；Legality ${formatMetric(selectedDeploymentRow.legality_score)} · Priority ${formatMetric(selectedDeploymentRow.priority_score)} · Economy ${formatMetric(selectedDeploymentRow.economy_score)}`} />
                        </Box>
                      </Box>
                      <Box className="detail-group">
                        <Typography variant="subtitle2">Failure Breakdown</Typography>
                        <Typography className="detail-group-explanation" variant="body2">
                          這一段回答「沒有通過的原因是哪一類」。所有數值都是該類問題在 executed trials 中的比例；它們是問題定位資訊，不是額外加總成一個總分。
                        </Typography>
                        <Box className="detail-metric-grid failure-grid">
                          <DetailMetric label="Invalid Output" value={selectedDeploymentRow.invalid_output_rate} note={`M6 輸出格式或 contract 不合格 · n=${formatCount(selectedDeploymentRow.executed_trial_count)}`} />
                          <DetailMetric label="M6 Contract Violation" value={selectedDeploymentRow.m6_contract_violation_rate ?? null} note="action 欄位、source、target 或 count 不符合 M6 contract。" />
                          <DetailMetric label="M6 Decision Infeasible" value={selectedDeploymentRow.m6_decision_infeasible_rate ?? null} note="M6 找不到合法 target，或無法完成要求的人流分配。" />
                          <DetailMetric label="Rule Violation" value={selectedDeploymentRow.rule_violation_rate} note={`M7 判斷 action 不符合場域規則 · n=${formatCount(selectedDeploymentRow.executed_trial_count)}`} />
                          <DetailMetric label="Capacity Violation" value={selectedDeploymentRow.capacity_violation_rate} note={`M7 判斷節點 post-population 超過 capacity · n=${formatCount(selectedDeploymentRow.executed_trial_count)}`} />
                          <DetailMetric label="Topology Violation" value={selectedDeploymentRow.topology_violation_rate} note={`M7 判斷 source → target 不符合 topology · n=${formatCount(selectedDeploymentRow.executed_trial_count)}`} />
                        </Box>
                      </Box>
                    </Stack>
                  </details>
                </Stack>
              )}
              <details
                className="boundary-analysis-box"
                onToggle={(event) => {
                  setBoundaryInlineOpen((event.currentTarget as HTMLDetailsElement).open);
                }}
              >
                <summary>Perception Error Boundary</summary>
                <Typography variant="body2" color="text.secondary" className="boundary-analysis-intro">
                  這是針對目前 topology、model、regime、規則來源與決策方式的條件式敏感度分析。它使用正式 M5 residual 重播 M6/M7，不修改正式結果，也不是通用的模型 accuracy 門檻。
                </Typography>
                <BoundaryAnalysisContent
                  selection={boundarySelection}
                  payload={boundaryPayload}
                  capability={boundaryCapability}
                  job={boundaryJob}
                  loading={boundaryLoading}
                  sweepStarting={boundarySweepStarting}
                  error={boundaryError}
                  expectedSelection={selectedBoundarySelection()}
                  onStartSweep={() => void startBoundarySweep()}
                  downloadJsonUrl={boundaryDownloadUrl("json")}
                  downloadMarkdownUrl={boundaryDownloadUrl("md")}
                />
              </details>
            </Box>
          )}

          <Box className="section">
            <Stack className="paper-header" spacing={1.5}>
              {run?.status === "PARTIAL_QUOTA_EXHAUSTED" && (
                <Alert severity="warning">
                  這是部分完成 Run：已完成的 ideal／deployment 配對會保留並顯示；額度耗盡後尚未執行的 trial 不會填成 0。恢復額度後可從 Run 狀態區 Resume。
                </Alert>
              )}
              <Box className="paper-header-title">
                <Typography variant="h6">Paper View</Typography>
                <Typography className="section-purpose" variant="body2">
                  獨立選擇一組 topology × model × regime，每列查看一組 w/o ↔ w/ paired reliability 與 deployment 診斷結果。
                </Typography>
                <Typography color="text.secondary">Paper View · {paperTitle}</Typography>
                <Typography variant="body2" color="text.secondary" className="section-caption">
                  Paper View 的條件與 Deployment Comparison Index、Selected Configuration Detail 分開；矩陣操作不會改變這裡的結果。每列固定代表同一組 scenario/trial 的 w/o ↔ w/ 配對，ALL 不平均、不重算。
                </Typography>
              </Box>
              <Box className="paper-condition-selector">
                <Typography variant="subtitle2">Paper View 實驗條件</Typography>
                <Typography variant="caption" color="text.secondary">
                  先選擇要查看的 Topology × Perception model × Regime；表格每列會合併同一條件的 ideal 與 deployment rows。
                </Typography>
                <Stack className="paper-condition-row" direction="row" spacing={1}>
                  <FormControl size="small" disabled={!run}>
                    <InputLabel>Topology</InputLabel>
                    <Select label="Topology" value={paperTopologyId} onChange={(event) => changePaperTopology(event.target.value)}>
                      <MenuItem value={allFilter}>ALL</MenuItem>
                      {paperTopologyOptions.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <FormControl size="small" disabled={!run}>
                    <InputLabel>Perception model</InputLabel>
                    <Select label="Perception model" value={paperModelId} onChange={(event) => changePaperModel(event.target.value)}>
                      <MenuItem value={allFilter}>ALL</MenuItem>
                      {paperModelOptions.map((item) => <MenuItem key={item.model_id} value={item.model_id}>{item.model_name}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <FormControl size="small" disabled={!run}>
                    <InputLabel>Regime</InputLabel>
                    <Select label="Regime" value={paperRegime} onChange={(event) => setPaperRegime(event.target.value)}>
                      <MenuItem value={allFilter}>ALL</MenuItem>
                      {paperRegimeOptions.map((item) => <MenuItem key={item} value={item}>{item}</MenuItem>)}
                    </Select>
                  </FormControl>
                </Stack>
              </Box>
              <Stack className="paper-header-controls" direction={{ xs: "column", lg: "row" }} spacing={1.5}>
                <Stack className="paper-filter-row" direction="row" spacing={1} flexWrap="wrap">
                  <FormControl size="small" sx={{ minWidth: 170 }} disabled={paperRows.length === 0}>
                    <InputLabel>Rule Source</InputLabel>
                    <Select label="Rule Source" value={paperRuleSource} onChange={(event) => setPaperRuleSource(event.target.value)}>
                      <MenuItem value={allFilter}>ALL</MenuItem>
                      {ruleSourceOptions.map((item) => <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <FormControl size="small" sx={{ minWidth: 180 }} disabled={paperRows.length === 0}>
                    <InputLabel>決策方式</InputLabel>
                    <Select label="決策方式" value={paperDecisionInterface} onChange={(event) => setPaperDecisionInterface(event.target.value)}>
                      <MenuItem value={allFilter}>ALL</MenuItem>
                      {decisionInterfaceOptions.map((item) => <MenuItem key={item} value={item}>{displayDecisionInterface(item)}</MenuItem>)}
                    </Select>
                  </FormControl>
                </Stack>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Button variant="outlined" size="small" startIcon={<ScienceIcon />} onClick={openPaperTopologyPreview} disabled={!paperCanOpenPreview}>
                    Open topology preview
                  </Button>
                  <Button variant="outlined" size="small" onClick={() => void openPaperBoundary()} disabled={!paperCanOpenPreview}>
                    查看 error boundary
                  </Button>
                  {run && <DownloadButtons run={run} />}
                </Stack>
              </Stack>
              {!paperCanOpenPreview && paperRows.length > 0 && (
                <Typography variant="caption" color="text.secondary">
                  要從 Paper View 開啟單一 trial preview，請選定單一 Topology、Model、Regime、Rule Source 與決策方式。
                </Typography>
              )}
            </Stack>
            <Box className="paper-column-selector">
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} justifyContent="space-between" alignItems={{ sm: "center" }}>
                <Box>
                  <Typography variant="subtitle2">Paper View 表格欄位</Typography>
                  <Typography variant="caption" color="text.secondary">R_ideal、R_deploy、Delta R 固定顯示；目前另選欄位：{paperVisibleColumns.length} / {paperColumnDefinitions.length}。</Typography>
                </Box>
                <Stack direction="row" spacing={0.5}>
                  <Button size="small" variant="text" onClick={() => setPaperVisibleColumns(paperColumnDefinitions.map((column) => column.id))} disabled={paperRows.length === 0}>全部欄位</Button>
                  <Button size="small" variant="text" onClick={() => setPaperVisibleColumns([...corePaperColumnIds])} disabled={paperRows.length === 0}>只看核心結果</Button>
                </Stack>
              </Stack>
              <Box className="paper-column-groups">
                {paperColumnGroups.map((group) => (
                  <Box className="paper-column-group" key={group.id}>
                    <Typography variant="caption" className="paper-column-group-title">{group.label}</Typography>
                    <Stack className="paper-column-options" direction="row" spacing={0.5} flexWrap="wrap">
                      {paperColumnDefinitions.filter((column) => column.group === group.id).map((column) => (
                        <Box component="label" className="paper-column-option" key={column.id}>
                          <Checkbox
                            size="small"
                            checked={paperVisibleColumns.includes(column.id)}
                            onChange={(event) => togglePaperColumn(column.id, event.target.checked)}
                            disabled={paperRows.length === 0}
                          />
                          <Typography variant="body2">{column.label}</Typography>
                        </Box>
                      ))}
                    </Stack>
                  </Box>
                ))}
              </Box>
            </Box>
            <TableContainer className="paper-table">
              <Table size="small" aria-label="paper view table">
                <TableHead>
                  <TableRow>
                    <TableCell>Rule Source</TableCell>
                    <TableCell>實驗情境</TableCell>
                    <TableCell>決策方式</TableCell>
                    <TableCell>Regime</TableCell>
                    <TableCell>R_ideal</TableCell>
                    <TableCell>R_deploy</TableCell>
                    <TableCell>Delta R</TableCell>
                    {visiblePaperColumnDefinitions.map((column) => <TableCell key={column.id}>{column.label}</TableCell>)}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {paperPairedRows.length === 0 && (
                    <TableRow><TableCell colSpan={paperColumnCount}>{run ? "目前 Paper View 條件沒有結果。" : "請先載入一個成功的 Run。"}</TableCell></TableRow>
                  )}
                  {paperPairedRows.length > 0 && filteredPaperPairedRows.length === 0 && (
                    <TableRow><TableCell colSpan={paperColumnCount}>目前篩選沒有符合結果。</TableCell></TableRow>
                  )}
                  {filteredPaperPairedRows.map((row) => (
                    <TableRow
                      key={row.pairKey}
                    >
                      <TableCell>{row.rule_source_label}</TableCell>
                      <TableCell>
                        w/o ↔ w/
                        <span className="paper-paired-context-note">同一組 scenario/trial 配對</span>
                      </TableCell>
                      <TableCell>{displayDecisionInterface(row.decision_interface)}</TableCell>
                      <TableCell>{row.ground_truth_regime}</TableCell>
                      <TableCell className="paper-reliability-cell">
                        <strong>{displayPaperPairedMetric(row, row.r_ideal)}</strong>
                        <small>w/o · scenario_gt</small>
                      </TableCell>
                      <TableCell className="paper-reliability-cell">
                        <strong>{displayPaperPairedMetric(row, row.r_deploy)}</strong>
                        <small>w/ · observed_population</small>
                      </TableCell>
                      <TableCell className="paper-reliability-cell">
                        <strong>{displayPaperPairedMetric(row, row.delta_r)}</strong>
                        <small>R_ideal − R_deploy</small>
                        {row.interpretation.code !== "AVAILABLE" && (
                          <small className="paper-paired-status">{row.interpretation.label}</small>
                        )}
                      </TableCell>
                      {visiblePaperColumnDefinitions.map((column) => <TableCell key={column.id}>{column.render(row)}</TableCell>)}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>

          <Dialog open={boundaryDialogOpen} onClose={() => setBoundaryDialogOpen(false)} fullWidth maxWidth="lg">
            <DialogTitle>Perception Error Boundary</DialogTitle>
            <DialogContent dividers>
              <Typography variant="body2" color="text.secondary" className="boundary-analysis-intro">
                下方是目前 Paper View 條件的唯讀敏感度分析。它不會改變 Deployment Comparison Index 或 Selected Configuration Detail 的選取。
              </Typography>
              <BoundaryAnalysisContent
                selection={boundarySelection}
                payload={boundaryPayload}
                capability={boundaryCapability}
                job={boundaryJob}
                loading={boundaryLoading}
                sweepStarting={boundarySweepStarting}
                error={boundaryError}
                expectedSelection={boundaryDialogSelection}
                onStartSweep={() => void startBoundarySweep()}
                downloadJsonUrl={boundaryDownloadUrl("json")}
                downloadMarkdownUrl={boundaryDownloadUrl("md")}
              />
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setBoundaryDialogOpen(false)}>關閉</Button>
            </DialogActions>
          </Dialog>

          <details className="advanced-box">
            <summary>進階資訊</summary>
            <Typography variant="body2" component="div">
              <ul>
                <li>Framework Condition 固定比較 w/o 理想基線與 w/ 受正式 perception 殘差影響的部署分支；兩者使用相同 scenario_gt 配對。</li>
                 <li>Risk Consistency 使用 F-beta；本次 run 的 β={displayedRiskFBeta}。β 大於 1 時，漏掉真正高風險來源會有較高影響。</li>
                 {metadata && <li>Scenario：Beta({metadata.default_config.scenario_alpha}, {metadata.default_config.scenario_beta})、hotspot ratio {metadata.default_config.rho}、風險門檻 {metadata.default_config.risk_threshold}。</li>}
                  {metadata && <li>Scenario generation：{run?.config.scenario_policy_version ?? metadata.default_config.scenario_policy_version}；共同 feasibility oracle={run?.scenario_generation?.feasibility_oracle_version ?? "unavailable"}；只將通過介面無關可行性 gate 的候選納入正式 scenario，拒絕候選會保留在 diagnostics。</li>}
                 {metadata && <li>M6 Decision Policy：{run?.config.decision_policy_version ?? metadata.default_config.decision_policy_version}；同一個 capacity-aware multi-source planner 同時處理 ideal 與 deployment。</li>}
                 <li>Rule source comparison：人工規則與 AI 生成 rule bundle 共用 scenario／observation；M7 固定使用人工 gold-standard。GAI 未設定 provider 時為 unavailable，不填 0。</li>
                 <li>GAI request／response checksum、HTTP status、finish reason、safety block 與 invalid output evidence 保存在 M6/gai_decision_trace.jsonl；UI 不顯示 API key。</li>
                 <li>M7 驗證使用 M4 scenario_gt，不把 M6 observation 當真值。</li>
                <li>輸出限制會寫入 delivery manifest 與 reproducibility manifest。</li>
              </ul>
            </Typography>
          </details>
        </Stack>
      </Container>
    </Box>
  );
}

function ParameterHint({ label, text }: { label: string; text: string }) {
  return (
    <InputAdornment position="end">
      <Tooltip title={text} arrow>
        <IconButton aria-label={`${label} 說明`} edge="end" size="small">
          <HelpOutlineIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </InputAdornment>
  );
}

function SectionHint({ label, text }: { label: string; text: string }) {
  return (
    <Tooltip title={text} arrow>
      <IconButton aria-label={label} size="small">
        <HelpOutlineIcon fontSize="small" />
      </IconButton>
    </Tooltip>
  );
}

function normalizeMultiSelectValue(value: unknown, allIds: string[]): string[] {
  const values = (Array.isArray(value) ? value : [value]).map(String);
  if (values.includes(allFilter)) return [...allIds];
  return [...new Set(values.filter((item) => allIds.includes(item)))];
}

function MatrixCellTooltip({ row, metric, regime, run }: { row?: MatrixRow | AggregateMetricRow; metric: string; regime: string; run: RunSummary | null }) {
  if (!row) return "尚未執行此條件。";
  const decisionInterface = "decision_interface" in row ? row.decision_interface : "rule_based";
  return (
    <Box className="metric-tooltip">
      <strong>{metricLabel(metric)}</strong>
      <span>Scope: {displayRuleSource(row.rule_source_id)} × topology × model × regime × {displayDecisionInterface(decisionInterface)} · deployment</span>
      <span>Framework: w/ Two-stage framework</span>
      <span>Regime: {regime} · Executed trials: {formatCount(row.trial_count)}</span>
      <span>Metric policy: v{run?.config.metric_policy_version ?? "unavailable"}</span>
      {metric === "risk_consistency" && <span>Risk β: {formatMetric(row.risk_f_beta)}</span>}
      {metric === "delta_r" && <span>Delta R scope: paired R_ideal - R_deploy</span>}
    </Box>
  );
}

function getMatrixMetric(row: MatrixRow | AggregateMetricRow | undefined, metric: string): unknown {
  if (!row) return null;
  return (row as unknown as Record<string, unknown>)[metric] ?? null;
}

function BaselineValue({ cell }: { cell: BaselineCell }) {
  const valueLabel = cell.inconsistent ? "無單一代表值" : formatMetric(cell.value);
  return (
    <span className={`baseline-value baseline-value-${cell.interpretation.code.toLowerCase()}`} title={cell.interpretation.message}>
      <strong>{valueLabel}</strong>
      <small>n={formatCount(cell.executedTrialCount)}</small>
      {cell.shared && <small className="baseline-scope">共用 ideal reference · 不含 model</small>}
      {!cell.shared && cell.modelCount > 1 && <small className="baseline-scope">model rows={cell.modelCount} · 舊 Run</small>}
      <small className="baseline-status">{cell.interpretation.label}</small>
    </span>
  );
}

function BaselineMatrix({ title, description, rows, ariaLabel }: { title: string; description: string; rows: BaselineRow[]; ariaLabel: string }) {
  return (
    <Box className="baseline-source-panel">
      <Typography variant="subtitle1">{title}</Typography>
      <Typography variant="body2" color="text.secondary" className="baseline-source-description">{description}</Typography>
      <TableContainer className="baseline-table">
        <Table size="small" aria-label={ariaLabel}>
          <TableHead>
            <TableRow>
              <TableCell>Topology</TableCell>
              {regimes.map((item) => <TableCell key={item}>{item}</TableCell>)}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map(({ topology, cells }) => (
              <TableRow key={topology.id}>
                <TableCell>{topology.name}</TableCell>
                {cells.map((cell, index) => (
                  <TableCell key={`${topology.id}-${regimes[index]}`}>
                    <BaselineValue cell={cell} />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

function LegacyBoundaryAnalysisContent({
  selection,
  expectedSelection,
  payload,
  loading,
  error,
  downloadJsonUrl,
  downloadMarkdownUrl,
}: {
  selection: BoundarySelection | null;
  expectedSelection: BoundarySelection | null;
  payload: BoundaryPayload | null;
  loading: boolean;
  error: string | null;
  downloadJsonUrl: string | null;
  downloadMarkdownUrl: string | null;
}) {
  const sameSelection = Boolean(selection && expectedSelection && (
    selection.ruleSourceId === expectedSelection.ruleSourceId
    && selection.topologyId === expectedSelection.topologyId
    && selection.modelId === expectedSelection.modelId
    && selection.regime === expectedSelection.regime
    && selection.decisionInterface === expectedSelection.decisionInterface
  ));
  if (loading || (payload && !sameSelection)) {
    return <LinearProgress className="boundary-loading" />;
  }
  if (error) return <Alert severity="error" className="section-alert">{error}</Alert>;
  if (!payload) {
    return <Typography variant="body2" color="text.secondary">展開後載入目前條件的 Boundary 分析。</Typography>;
  }
  const empirical = payload.empirical_boundary;
  const analytical = payload.analytical_boundary;
  const interpretation = payload.interpretation;
  const positiveTarget = empirical?.target_thresholds?.find((item) => item.target === "R_deploy > 0");
  return (
    <Stack className="boundary-analysis-content" spacing={1.5}>
      {interpretation?.status === "baseline_failed" && (
        <Alert severity="warning">Ideal baseline failed — perception boundary is not interpretable。正確人數輸入已無法通過 M7，不能把後續改善直接歸因於 perception。</Alert>
      )}
      {empirical?.status === "unavailable" && (
        <Alert severity="info">{empirical.unavailable_reason ?? "Empirical boundary unavailable。"}</Alert>
      )}
      {interpretation?.status !== "baseline_failed" && interpretation?.message && (
        <Alert severity="info">{interpretation.message}</Alert>
      )}
      <Box className="boundary-context-grid">
        <Typography variant="body2"><strong>條件：</strong>{payload.context.topology_id} × {payload.context.model_id} × {payload.context.regime}</Typography>
        <Typography variant="body2"><strong>規則來源：</strong>{displayRuleSource(payload.context.rule_source_id)}</Typography>
        <Typography variant="body2"><strong>決策：</strong>{displayDecisionInterface(payload.context.decision_interface)}</Typography>
        <Typography variant="body2"><strong>M7 標準：</strong>{displayRuleSource(payload.context.validation_rule_source_id ?? HUMAN_RULE_SOURCE)}</Typography>
      </Box>
      {empirical?.status !== "unavailable" && (
        <Box>
          <Typography variant="subtitle2">主要 Boundary</Typography>
          <Box className="boundary-result-grid">
            <DetailMetric label="目前 residual scale" value={empirical?.actual_alpha ?? null} note="α=1 代表正式 M5 residual；α=0 代表 ideal ground-truth input。" />
            <DetailMetric label="R_deploy &gt; 0 最大 α" value={empirical?.max_alpha_for_positive_r ?? null} note="在已保存 empirical residual 下，仍有至少一個 trial 通過 M7 的最大誤差比例。" />
            <DetailMetric label="需要降低 residual" value={empirical?.required_residual_reduction_for_positive_r ?? null} note="這是 residual magnitude 的降低比例，不是 perception accuracy 提升百分比。" />
          </Box>
          {!positiveTarget && <Typography variant="body2" color="text.secondary">目前評估範圍內沒有找到 R_deploy &gt; 0 的 residual scale。</Typography>}
        </Box>
      )}
      {empirical?.target_thresholds && empirical.target_thresholds.length > 0 && (
        <Box className="boundary-table-wrap">
          <Typography variant="subtitle2">Reliability target</Typography>
          <Table size="small" aria-label="perception error boundary targets">
            <TableHead><TableRow><TableCell>目標</TableCell><TableCell>最大 residual scale</TableCell><TableCell>需要降低誤差</TableCell><TableCell>結果</TableCell></TableRow></TableHead>
            <TableBody>{empirical.target_thresholds.map((item) => (
              <TableRow key={item.target}>
                <TableCell>{item.target}</TableCell>
                <TableCell>{formatMetric(item.max_alpha)}</TableCell>
                <TableCell>{item.required_residual_reduction == null ? "unavailable" : `${(item.required_residual_reduction * 100).toFixed(1)}%`}</TableCell>
                <TableCell>{item.status === "reached" ? "reached" : "not reached"}</TableCell>
              </TableRow>
            ))}</TableBody>
          </Table>
        </Box>
      )}
      {analytical?.sources && analytical.sources.length > 0 && (
        <details className="boundary-subsection">
          <summary>Analytical source boundary</summary>
          <Typography variant="body2" color="text.secondary">這些是 M6 risk／move count 的整數門檻，不等於 M7 一定通過。</Typography>
          <Table size="small" aria-label="analytical source boundary">
            <TableHead><TableRow><TableCell>Source</TableCell><TableCell>GT 人數</TableCell><TableCell>Capacity</TableCell><TableCell>首個 high-risk 人數</TableCell><TableCell>可容許低估</TableCell></TableRow></TableHead>
            <TableBody>{analytical.sources.map((source) => (
              <TableRow key={source.source_id}>
                <TableCell>{source.source_id}</TableCell>
                <TableCell>{source.ground_truth_population}</TableCell>
                <TableCell>{source.capacity}</TableCell>
                <TableCell>{source.first_high_risk_count}</TableCell>
                <TableCell>{source.signed_error_boundary}</TableCell>
              </TableRow>
            ))}</TableBody>
          </Table>
          <Typography variant="body2" color="text.secondary">可展開查看每個 source 的 requested move count 整數轉換門檻：</Typography>
          {analytical.sources.map((source) => (
            <details key={`${source.source_id}-move-transitions`} className="boundary-subsection boundary-inline-detail">
              <summary>{source.source_id} · requested move count transitions</summary>
              <Table size="small" aria-label={`${source.source_id} requested move count transitions`}>
                <TableHead><TableRow><TableCell>Observed population</TableCell><TableCell>Requested move count</TableCell></TableRow></TableHead>
                <TableBody>{(source.requested_move_transition_points ?? []).map((point) => (
                  <TableRow key={`${point.observed_population}-${point.requested_move_count}`}>
                    <TableCell>{point.observed_population}</TableCell>
                    <TableCell>{point.requested_move_count}</TableCell>
                  </TableRow>
                ))}</TableBody>
              </Table>
            </details>
          ))}
        </details>
      )}
      {empirical?.alpha_points && empirical.alpha_points.length > 0 && (
        <Box className="boundary-table-wrap">
          <Typography variant="subtitle2">Empirical sensitivity curve</Typography>
          <Typography variant="body2" color="text.secondary">α=0 是 ideal；α=1 是正式 M5 observation。中間數值是使用同一批 residual 的 counterfactual sensitivity。</Typography>
          <Table size="small" aria-label="empirical perception sensitivity">
            <TableHead><TableRow><TableCell>α</TableCell><TableCell>R_deploy</TableCell><TableCell>Valid / executed</TableCell><TableCell>主要 M7 evidence</TableCell></TableRow></TableHead>
            <TableBody>{empirical.alpha_points.map((point) => (
              <TableRow key={`${point.alpha}-${point.label}`}>
                <TableCell>{point.alpha.toFixed(6)}</TableCell>
                <TableCell>{formatMetric(point.r_deploy)}</TableCell>
                <TableCell>{point.valid_trial_count} / {point.executed_trial_count}</TableCell>
                <TableCell>{Object.entries(point.violation_reason_counts ?? {}).map(([code, count]) => `${code}: ${count}`).join("、") || "none"}</TableCell>
              </TableRow>
            ))}</TableBody>
          </Table>
        </Box>
      )}
      {payload.critical_evidence && payload.critical_evidence.length > 0 && (
        <Box className="boundary-subsection">
          <Typography variant="subtitle2">Critical violation evidence</Typography>
          <Typography variant="body2" color="text.secondary">以下例證直接來自 counterfactual M7 evidence；沒有提供的欄位不自行推導。</Typography>
          <Table size="small" aria-label="critical violation evidence">
            <TableHead><TableRow><TableCell>Reason</TableCell><TableCell>出現次數</TableCell><TableCell>代表性節點／流向／數值</TableCell></TableRow></TableHead>
            <TableBody>{payload.critical_evidence.map((item) => (
              <TableRow key={item.reason_code}>
                <TableCell>{item.reason_code}</TableCell>
                <TableCell>{item.count}</TableCell>
                <TableCell>{(item.examples ?? []).map(formatBoundaryEvidenceExample).join("；") || "M7 未提供"}</TableCell>
              </TableRow>
            ))}</TableBody>
          </Table>
        </Box>
      )}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        {downloadJsonUrl && <Button component="a" href={downloadJsonUrl} download size="small" startIcon={<DownloadIcon />}>下載 Boundary JSON</Button>}
        {downloadMarkdownUrl && <Button component="a" href={downloadMarkdownUrl} download size="small" startIcon={<DownloadIcon />}>下載 Boundary Markdown</Button>}
      </Stack>
    </Stack>
  );
}

function BoundaryAnalysisContent({
  selection,
  expectedSelection,
  payload,
  capability,
  job,
  loading,
  sweepStarting,
  error,
  onStartSweep,
  downloadJsonUrl,
  downloadMarkdownUrl,
}: {
  selection: BoundarySelection | null;
  expectedSelection: BoundarySelection | null;
  payload: BoundaryPayload | null;
  capability: BoundaryCapability | null;
  job: BoundaryJob | null;
  loading: boolean;
  sweepStarting: boolean;
  error: string | null;
  onStartSweep: () => void;
  downloadJsonUrl: string | null;
  downloadMarkdownUrl: string | null;
}) {
  const sameSelection = Boolean(selection && expectedSelection && (
    selection.ruleSourceId === expectedSelection.ruleSourceId
    && selection.topologyId === expectedSelection.topologyId
    && selection.modelId === expectedSelection.modelId
    && selection.regime === expectedSelection.regime
    && selection.decisionInterface === expectedSelection.decisionInterface
  ));
  if (loading || (payload && !sameSelection)) return <LinearProgress className="boundary-loading" />;
  if (error) return <Alert severity="error" className="section-alert">{error}</Alert>;
  if (!payload) return <Typography variant="body2" color="text.secondary">展開後先讀取目前 Run 的 Observed Estimate；Boundary Sweep 需按下按鈕才會執行。</Typography>;

  const observed = payload.observed_estimate;
  const sweep = payload.boundary_sweep;
  const curve = sweep?.lambda_curve ?? [];
  const focusCurve = sweep?.focus_lambda_curve ?? [];
  const displaySourceCurve = focusCurve.length > 0 ? focusCurve : curve;
  const firstZeroIndex = displaySourceCurve.findIndex((point) => point.r_deploy != null && point.r_deploy <= 0);
  const displayCurve = firstZeroIndex >= 0 ? displaySourceCurve.slice(0, firstZeroIndex + 1) : displaySourceCurve;
  const targets = sweep?.targets ?? [];
  const interval = observed?.observed_error_interval;
  const errorSummary = observed?.error_summary;
  const hasIdealFailure = observed?.r_ideal != null && observed.r_ideal <= 0;
  const isRunning = Boolean(job && !["SUCCEEDED", "FAILED", "CANCELLED"].includes(job.status));
  return (
    <Stack className="boundary-analysis-content" spacing={1.5}>
      <Alert severity="info">
        這個分析只適用於目前選定的拓樸、模型、密度、規則來源與決策方式。它把人數誤差縮放後，觀察 M7 通過率如何變化，不是通用的模型 accuracy 門檻，也不是正式 M8/M9 結果。
      </Alert>
      {hasIdealFailure && <Alert severity="warning">Ideal baseline failed — perception boundary is not interpretable。正確人數輸入已無法通過 M7，不能把後續改善直接歸因於 perception。</Alert>}
      <Box className="boundary-context-grid">
        <Typography variant="body2"><strong>拓樸：</strong>{payload.context.topology_id}</Typography>
        <Typography variant="body2"><strong>模型：</strong>{payload.context.model_id}</Typography>
        <Typography variant="body2"><strong>密度：</strong>{payload.context.regime}</Typography>
        <Typography variant="body2"><strong>規則來源：</strong>{displayRuleSource(payload.context.rule_source_id)}</Typography>
        <Typography variant="body2"><strong>決策：</strong>{displayDecisionInterface(payload.context.decision_interface)}</Typography>
        <Typography variant="body2"><strong>M7 標準：</strong>{displayRuleSource(payload.context.validation_rule_source_id ?? HUMAN_RULE_SOURCE)}</Typography>
      </Box>

      <Box className="boundary-subsection">
        <Typography variant="subtitle2">Observed Estimate · 既有 Run 觀察估計</Typography>
        <Typography variant="body2" color="text.secondary">直接讀取已保存的 M5 observation 與 M7 trial facts，不重跑 M5～M8。</Typography>
        {observed ? (
          <>
            <Box className="boundary-result-grid">
              <DetailMetric label="R_deploy" value={observed.r_deploy ?? null} note={`${observed.valid_trial_count ?? 0} / ${observed.executed_trial_count ?? 0} trials 通過 M7`} />
              <DetailMetric label="MAE" value={errorSummary?.mae ?? null} note="實際 rounded observation 與 GT 的平均絕對誤差" />
              <DetailMetric label="P90 absolute error" value={errorSummary?.p90_absolute_error ?? null} note="90% 人數誤差落在此值以內" />
              <DetailMetric label="Max absolute error" value={errorSummary?.max_absolute_error ?? null} note="目前 Run 觀察到的最大絕對誤差" />
              <DetailMetric label="Underestimate rate" value={errorSummary?.underestimate_rate ?? null} note="實際 observation 低於 GT 的比例" />
              <DetailMetric label="Overestimate rate" value={errorSummary?.overestimate_rate ?? null} note="實際 observation 高於 GT 的比例" />
            </Box>
            <Typography variant="body2" className="boundary-note">{observed.interpretation ?? "這是既有 Run 的觀察範圍，不是精確臨界值。"}</Typography>
            <TableContainer className="boundary-table-wrap">
              <Table size="small" aria-label="observed error interval">
                <TableHead><TableRow><TableCell>成功 trial 最大誤差</TableCell><TableCell>失敗 trial 最小誤差</TableCell><TableCell>觀察結果</TableCell></TableRow></TableHead>
                <TableBody><TableRow>
                  <TableCell>{formatMetric(interval?.max_error_among_successful_trials ?? null)}</TableCell>
                  <TableCell>{formatMetric(interval?.min_error_among_failed_trials ?? null)}</TableCell>
                  <TableCell>{interval?.status === "observed_interval" ? "Observed interval" : "沒有單一誤差門檻；可能與誤差位置、方向或拓樸餘裕有關"}</TableCell>
                </TableRow></TableBody>
              </Table>
            </TableContainer>
            {Object.keys(observed.violation_reason_counts ?? {}).length > 0 && (
              <Typography variant="body2" color="text.secondary">主要 M7 違規：{Object.entries(observed.violation_reason_counts ?? {}).map(([code, count]) => `${code} ${count}`).join("、")}</Typography>
            )}
          </>
        ) : (
          <Typography variant="body2" color="text.secondary">Observed Estimate unavailable：缺少完整 trial lineage。</Typography>
        )}
      </Box>

      <Box className="boundary-subsection">
        <Typography variant="subtitle2">Computed Boundary · Boundary Sweep</Typography>
        <Typography variant="body2" color="text.secondary">明確按下開始後，使用同一批 scenario、residual、seed，以 λ=0.00～1.00、每 0.05 一點重跑 Rule-based M5→M6→M7。</Typography>
        <Typography variant="body2" color="text.secondary">λ=0 是正確人數；λ=1 是正式 M5 observation。結果會保存於獨立 Boundary Job，不修改原 Run。</Typography>
        {selection?.decisionInterface !== "rule_based" ? (
          <Alert severity="info" className="section-alert">Computed Boundary unavailable — GAI replay is not enabled。GAI 只顯示 Existing Run 的已保存結果。</Alert>
        ) : !capability?.boundary_sweep.available ? (
          <Alert severity="warning" className="section-alert">{capability?.boundary_sweep.reason ?? "目前條件沒有足夠的 residual 或 scenario lineage。"}</Alert>
        ) : isRunning ? (
          <Box className="boundary-job-status">
            <Typography variant="body2"><strong>狀態：</strong>{job?.status} · λ {job?.current_lambda?.toFixed(2) ?? "-"}</Typography>
            <LinearProgress variant="determinate" value={job?.total_lambda_count ? ((job.completed_lambda_count ?? 0) / job.total_lambda_count) * 100 : 0} />
            <Typography variant="caption" color="text.secondary">已完成 {job?.completed_lambda_count ?? 0} / {job?.total_lambda_count ?? 21} 個 lambda 點</Typography>
          </Box>
        ) : sweep?.status === "available" && curve.length > 0 ? (
          <>
            {sweep.monotonicity?.warning_code && <Alert severity="warning" className="section-alert">{sweep.monotonicity.warning_code}：可靠度曲線不是單調下降；主要結果使用 conservative safe boundary。</Alert>}
            {sweep.focus?.status === "FIRST_ZERO_REACHED" && (
              <Typography variant="body2" color="text.secondary" className="boundary-note">
                目前先顯示從 λ=0.00 到第一個 R_deploy=0 的細部區間，步距為 0.01；完整 0.00～1.00 曲線仍保留在下方。
              </Typography>
            )}
            <TableContainer className="boundary-table-wrap">
              <Table size="small" aria-label="computed boundary curve">
                <TableHead><TableRow><TableCell>λ</TableCell><TableCell>R_deploy</TableCell><TableCell>Valid / Executed</TableCell><TableCell>MAE</TableCell><TableCell>P90</TableCell><TableCell>Max</TableCell><TableCell>主要違規</TableCell></TableRow></TableHead>
                <TableBody>{displayCurve.map((point) => <TableRow key={point.lambda}>
                  <TableCell>{point.lambda.toFixed(2)}</TableCell>
                  <TableCell>{formatMetric(point.r_deploy)}</TableCell>
                  <TableCell>{point.valid_trial_count} / {point.executed_trial_count}</TableCell>
                  <TableCell>{formatMetric(point.error_summary?.mae ?? null)}</TableCell>
                  <TableCell>{formatMetric(point.error_summary?.p90_absolute_error ?? null)}</TableCell>
                  <TableCell>{formatMetric(point.error_summary?.max_absolute_error ?? null)}</TableCell>
                  <TableCell>{Object.entries(point.violation_reason_counts ?? {}).map(([code, count]) => `${code}: ${count}`).join("、") || "none"}</TableCell>
                </TableRow>)}</TableBody>
              </Table>
            </TableContainer>
            {focusCurve.length > 0 && (
              <details className="boundary-subsection boundary-inline-detail">
                <summary>查看完整 0.00～1.00 粗略曲線（step 0.05）</summary>
                <TableContainer className="boundary-table-wrap">
                  <Table size="small" aria-label="complete computed boundary curve">
                    <TableHead><TableRow><TableCell>λ</TableCell><TableCell>R_deploy</TableCell><TableCell>Valid / Executed</TableCell><TableCell>主要違規</TableCell></TableRow></TableHead>
                    <TableBody>{curve.map((point) => <TableRow key={`full-${point.lambda}`}>
                      <TableCell>{point.lambda.toFixed(2)}</TableCell>
                      <TableCell>{formatMetric(point.r_deploy)}</TableCell>
                      <TableCell>{point.valid_trial_count} / {point.executed_trial_count}</TableCell>
                      <TableCell>{Object.entries(point.violation_reason_counts ?? {}).map(([code, count]) => `${code}: ${count}`).join("、") || "none"}</TableCell>
                    </TableRow>)}</TableBody>
                  </Table>
                </TableContainer>
              </details>
            )}
            <TableContainer className="boundary-table-wrap">
              <Typography variant="subtitle2">Reliability targets</Typography>
              <Table size="small" aria-label="computed boundary targets">
                <TableHead><TableRow><TableCell>目標</TableCell><TableCell>安全最大 λ</TableCell><TableCell>需要降低 residual</TableCell><TableCell>狀態</TableCell></TableRow></TableHead>
                <TableBody>{targets.map((target) => <TableRow key={target.target}>
                  <TableCell>{target.target}</TableCell>
                  <TableCell>{formatMetric(target.safe_critical_lambda ?? null)}</TableCell>
                  <TableCell>{target.required_error_reduction_pct == null ? "unavailable" : `${target.required_error_reduction_pct.toFixed(1)}%`}</TableCell>
                  <TableCell>{target.status}</TableCell>
                </TableRow>)}</TableBody>
              </Table>
            </TableContainer>
          </>
        ) : (
          <Button variant="contained" size="small" startIcon={<ScienceIcon />} onClick={onStartSweep} disabled={sweepStarting}>
            {sweepStarting ? "建立 Boundary Sweep…" : "開始 Boundary Sweep"}
          </Button>
        )}
      </Box>

      {downloadJsonUrl && <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <Button component="a" href={downloadJsonUrl} download size="small" startIcon={<DownloadIcon />}>下載 Boundary JSON</Button>
        {downloadMarkdownUrl && <Button component="a" href={downloadMarkdownUrl} download size="small" startIcon={<DownloadIcon />}>下載 Boundary Markdown</Button>}
      </Stack>}
    </Stack>
  );
}

function formatBoundaryEvidenceExample(example: BoundaryEvidenceExample): string {
  const location = example.node_id
    ? `node ${example.node_id}`
    : example.source_id || example.target_id
      ? `${example.source_id ?? "?"} → ${example.target_id ?? "?"}`
      : "global";
  const values = [
    example.post_population == null ? null : `post=${example.post_population}`,
    example.capacity == null ? null : `capacity=${example.capacity}`,
    example.message ?? null,
  ].filter((value): value is string => Boolean(value));
  return `${location}${values.length ? ` (${values.join(", ")})` : ""}`;
}

function DetailMetric({ label, value, note }: { label: string; value: number | null; note: string }) {
  return (
    <Box className="detail-metric">
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography className="detail-metric-value">{formatMetric(value)}</Typography>
      <Typography className="detail-metric-note" variant="caption" color="text.secondary">{note}</Typography>
    </Box>
  );
}

function buildPairedReliabilityGroups(rows: AggregateMetricRow[], regime?: string): PairedReliabilityGroup[] {
  const scopedRows = regime ? rows.filter((row) => row.ground_truth_regime === regime) : rows;
  const sourceKeys = [...new Set(scopedRows.map((row) => row.rule_source_id ?? HUMAN_RULE_SOURCE))];
  const interfaceKeys = [...new Set(scopedRows.map((row) => row.decision_interface))];
  const groups = sourceKeys.flatMap((sourceId) => interfaceKeys.map((decisionInterface) => {
    const pairRows = scopedRows.filter((row) => (
      (row.rule_source_id ?? HUMAN_RULE_SOURCE) === sourceId
      && row.decision_interface === decisionInterface
    ));
    const ideal = pairRows.find((row) => row.trial_type === "ideal" && row.framework_condition === "w/o Two-stage framework");
    const deployment = pairRows.find((row) => row.trial_type === "deployment" && row.framework_condition === "w/ Two-stage framework");
    const fields: Array<"r_ideal" | "r_deploy" | "delta_r"> = ["r_ideal", "r_deploy", "delta_r"];
    const inconsistentFields = ideal && deployment
      ? fields.filter((field) => !sameMetric(ideal[field], deployment[field]))
      : [];
    const unavailable = ideal?.availability === "unavailable" || deployment?.availability === "unavailable";
    const availability = unavailable ? "unavailable" : ideal && deployment ? "available" : "unavailable";
    const outcomeStatus = unavailable
      ? "unavailable"
      : ideal?.execution_outcome_status === "invalid_output" && deployment?.execution_outcome_status === "invalid_output"
        ? "invalid_output"
        : ideal?.execution_outcome_status === "decision_infeasible" && deployment?.execution_outcome_status === "decision_infeasible"
          ? "decision_infeasible"
          : "available";
    const error = inconsistentFields.length > 0
      ? `paired rows 的 ${inconsistentFields.join(", ")} 不一致。`
      : !ideal || !deployment ? "找不到完整的 ideal/deployment paired rows。" : null;
    const rIdeal = unavailable ? null : ideal?.r_ideal ?? null;
    const rDeploy = unavailable ? null : deployment?.r_deploy ?? null;
    const deltaR = unavailable ? null : deployment?.delta_r ?? null;
    return {
      id: `${sourceId}-${decisionInterface}`,
      ruleSourceId: sourceId,
      ruleSourceLabel: ideal?.rule_source_label ?? deployment?.rule_source_label ?? sourceId,
      decisionInterface,
      rIdeal,
      rDeploy,
      deltaR,
      idealCount: ideal?.executed_trial_count ?? null,
      deploymentCount: deployment?.executed_trial_count ?? null,
      availability,
      outcomeStatus,
      error,
      interpretation: getPairedInterpretation({ availability, error, rIdeal, rDeploy })
    };
  }));
  return groups.sort((left, right) => `${left.ruleSourceId}-${left.decisionInterface}`.localeCompare(`${right.ruleSourceId}-${right.decisionInterface}`));
}

function buildPaperPairedRows(rows: AggregateMetricRow[]): PaperPairedRow[] {
  type PairGroup = {
    topology_id: string;
    topology_name: string;
    model_id: string;
    model_name: string;
    ground_truth_regime: string;
    rule_source_id: string;
    rule_source_label: string;
    decision_interface: string;
    ideal: AggregateMetricRow | null;
    deployment: AggregateMetricRow | null;
  };

  const groups = new Map<string, PairGroup>();
  rows.forEach((row) => {
    const ruleSourceId = row.rule_source_id ?? HUMAN_RULE_SOURCE;
    const pairKey = [
      row.topology_id,
      row.model_id,
      row.ground_truth_regime,
      ruleSourceId,
      row.decision_interface
    ].join("::");
    const current = groups.get(pairKey) ?? {
      topology_id: row.topology_id,
      topology_name: row.topology_name,
      model_id: row.model_id,
      model_name: row.model_name,
      ground_truth_regime: row.ground_truth_regime,
      rule_source_id: ruleSourceId,
      rule_source_label: row.rule_source_label ?? displayRuleSource(ruleSourceId),
      decision_interface: row.decision_interface,
      ideal: null,
      deployment: null
    };
    if (row.framework_condition === "w/o Two-stage framework" && row.trial_type === "ideal") {
      current.ideal = current.ideal ?? row;
    }
    if (row.framework_condition === "w/ Two-stage framework" && row.trial_type === "deployment") {
      current.deployment = current.deployment ?? row;
    }
    groups.set(pairKey, current);
  });

  return [...groups.entries()]
    .map(([pairKey, group]) => {
      const { ideal, deployment } = group;
      const unavailable = !ideal
        || !deployment
        || ideal.availability === "unavailable"
        || deployment.availability === "unavailable";
      const inconsistentFields = !unavailable && ideal && deployment
        ? (["r_ideal", "r_deploy", "delta_r"] as const).filter((field) => !sameMetric(ideal[field], deployment[field]))
        : [];
      const error = unavailable
        ? null
        : inconsistentFields.length > 0
          ? `paired rows 的 ${inconsistentFields.join(", ")} 不一致。`
          : null;
      const available = !unavailable && !error;
      const rIdeal = available ? ideal?.r_ideal ?? null : null;
      const rDeploy = available ? deployment?.r_deploy ?? null : null;
      const deltaR = available ? deployment?.delta_r ?? null : null;
      return {
        pairKey,
        topology_id: group.topology_id,
        topology_name: group.topology_name,
        model_id: group.model_id,
        model_name: group.model_name,
        ground_truth_regime: group.ground_truth_regime,
        rule_source_id: group.rule_source_id,
        rule_source_label: group.rule_source_label,
        decision_interface: group.decision_interface,
        ideal,
        deployment,
        r_ideal: rIdeal,
        r_deploy: rDeploy,
        delta_r: deltaR,
        availability: unavailable ? "unavailable" : "available",
        outcomeStatus: unavailable
          ? "unavailable"
          : "available",
        error,
        interpretation: getPairedInterpretation({
          availability: unavailable ? "unavailable" : "available",
          error,
          rIdeal,
          rDeploy
        })
      } satisfies PaperPairedRow;
    })
    .sort((left, right) => left.pairKey.localeCompare(right.pairKey));
}

function displayPaperPairedMetric(row: PaperPairedRow, value: number | null): string {
  if (row.availability === "unavailable") return "unavailable";
  if (row.error) return "consistency error";
  return formatMetric(value);
}

function buildBaselineCell(
  rows: AggregateMetricRow[],
  topologyId: string,
  regime: string,
  ruleSourceId: string,
  decisionInterface: string
): BaselineCell {
  const candidates = rows.filter((row) => (
    row.topology_id === topologyId
    && row.ground_truth_regime === regime
    && row.framework_condition === "w/o Two-stage framework"
    && matchesBaselineDecisionInterface(row.decision_interface, decisionInterface)
    && (row.rule_source_id ?? HUMAN_RULE_SOURCE) === ruleSourceId
    && row.trial_type === "ideal"
    && row.availability === "available"
    && row.r_ideal !== null
  ));
  if (candidates.length === 0) {
    return {
      value: null,
      executedTrialCount: null,
      modelCount: 0,
      shared: false,
      inconsistent: false,
      interpretation: getIdealBaselineInterpretation(null, false, false)
    };
  }
  const reference = candidates[0];
  const inconsistent = candidates.some((row) => !sameMetric(reference.r_ideal, row.r_ideal));
  const value = inconsistent ? null : reference.r_ideal;
  const shared = !inconsistent && reference.ideal_baseline_scope?.includes("model_id excluded") === true;
  return {
    value,
    executedTrialCount: reference.executed_trial_count,
    modelCount: new Set(candidates.map((row) => row.model_id)).size,
    shared,
    inconsistent,
    interpretation: getIdealBaselineInterpretation(value, inconsistent, true)
  };
}

function matchesBaselineDecisionInterface(actual: string, selected: string): boolean {
  if (selected === "gai") return actual === "gai" || actual === "gai_reserved";
  return actual === selected;
}

function getIdealBaselineInterpretation(value: number | null, inconsistent: boolean, hasData: boolean): PairedInterpretation {
  if (!hasData) {
    return {
      code: "UNAVAILABLE",
      label: "Unavailable",
      message: "目前 Run 沒有此 rule source 的正式 ideal M8 row。"
    };
  }
  if (inconsistent) {
    return {
      code: "CONSISTENCY_ERROR",
      label: "Baseline consistency error",
      message: "這個 Run 的不同 model rows 有不同 R_ideal；不取平均，請到 Paper View 查看各 model 的原始 paired 結果。"
    };
  }
  if (!isNumericMetric(value)) {
    return {
      code: "CONSISTENCY_ERROR",
      label: "Baseline consistency error",
      message: "ideal baseline 缺少可用 R_ideal。"
    };
  }
  if (sameMetric(value, 0)) {
    return {
      code: "IDEAL_BASELINE_FAILED",
      label: "Ideal baseline failed",
      message: "R_ideal 已為 0；請查看 M7 violation evidence。"
    };
  }
  if (value > 0 && value < 1) {
    return {
      code: "IDEAL_BASELINE_PARTIAL",
      label: "Ideal baseline is partial",
      message: "R_ideal 未達 1；這是部分可行的 ideal baseline。"
    };
  }
  return {
    code: "AVAILABLE",
    label: "Interpretable baseline",
    message: "此 rule source 的 ideal validity 可作為 paired comparison reference。"
  };
}

function isNumericMetric(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function sameMetric(left: unknown, right: unknown): boolean {
  return isNumericMetric(left) && isNumericMetric(right) && Math.abs(left - right) <= metricTolerance;
}

function getPairedInterpretation({
  availability,
  error,
  rIdeal,
  rDeploy
}: {
  availability: string;
  error: string | null;
  rIdeal: number | null;
  rDeploy: number | null;
}): PairedInterpretation {
  if (availability === "unavailable") {
    return {
      code: "UNAVAILABLE",
      label: "Unavailable",
      message: "沒有正式 M7/M8 metrics，不以 0 代替。"
    };
  }
  if (error) {
    return {
      code: "CONSISTENCY_ERROR",
      label: "Consistency error",
      message: "paired rows 不一致，暫不解讀 Delta R。"
    };
  }
  if (!isNumericMetric(rIdeal) || !isNumericMetric(rDeploy)) {
    return {
      code: "CONSISTENCY_ERROR",
      label: "Consistency error",
      message: "paired reliability 缺少可用數值，暫不解讀 Delta R。"
    };
  }
  if (sameMetric(rIdeal, 0)) {
    return {
      code: "IDEAL_BASELINE_FAILED",
      label: "Ideal baseline failed",
      message: sameMetric(rDeploy, 0)
        ? "R_ideal 已為 0；Delta R 沒有可解讀的額外可靠度空間。"
        : "R_ideal 已為 0；deployment valid rate 高於失敗的 ideal baseline，Delta R 不能解讀為 perception degradation。"
    };
  }
  if (rIdeal > 0 && rIdeal < 1) {
    return {
      code: "IDEAL_BASELINE_PARTIAL",
      label: "Ideal baseline is partial",
      message: "R_ideal 未達 1；Delta R 是不完整 ideal baseline 上的條件性比較。"
    };
  }
  return {
    code: "AVAILABLE",
    label: "Interpretable paired comparison",
    message: "Delta R 可作為同一配對的 ideal/deployment 可靠度落差。"
  };
}

function uniqueRowValues(rows: AggregateMetricRow[], key: keyof AggregateMetricRow): string[] {
  return [...new Set(rows
    .map((row) => row[key])
    .filter((value): value is string | number => typeof value === "string" || typeof value === "number")
    .map(String))]
    .sort((left, right) => left.localeCompare(right));
}

function uniqueRuleSourceValues(rows: AggregateMetricRow[]): Array<{ id: string; label: string }> {
  const values = new Map<string, string>();
  rows.forEach((row) => {
    const id = row.rule_source_id ?? HUMAN_RULE_SOURCE;
    values.set(id, row.rule_source_label ?? displayRuleSource(id));
  });
  return [...values.entries()]
    .map(([id, label]) => ({ id, label }))
    .sort((left, right) => left.label.localeCompare(right.label));
}

function decisionInterfaceSortOrder(value: string): number {
  if (value === "rule_based") return 0;
  if (value === "gai") return 1;
  if (value === "gai_reserved") return 2;
  return 3;
}

function getDefaultDecisionInterface(rows: AggregateMetricRow[]): string {
  if (rows.some((row) => row.decision_interface === "rule_based")) return "rule_based";
  return rows[0]?.decision_interface ?? "rule_based";
}

function normalizeBaselineDecisionInterface(value: string): string {
  return value === "gai_reserved" ? "gai" : value;
}

function displayRuleSource(value?: string): string {
  if (value === HUMAN_RULE_SOURCE) return "人工規則";
  if (value === AI_RULE_SOURCE) return "AI 生成規則";
  return value ?? "unavailable";
}

function displayGaiProvider(runtime?: GaiRuntime): string {
  if (!runtime) return "unavailable";
  const provider = runtime.provider ?? "provider";
  const model = runtime.model ?? "model unavailable";
  return `${provider} / ${model}`;
}

function displayGaiStatus(runtime?: GaiRuntime): string {
  if (runtime?.execution_mode === "reserved_unavailable") return "unavailable · reserved"
  if (!runtime?.status || runtime.status === "unavailable") return "unavailable";
  if (runtime.status === "configured") return "configured";
  return runtime.status;
}

function displayDecisionInterface(value?: string): string {
  if (value === "rule_based") return "用 rule-based 做決策";
  if (value === "gai" || value === "gai_reserved") return "用 GAI 做決策";
  return value ?? "unavailable";
}

function displayAvailability(value?: string): string {
  if (value === "available") return "可用結果";
  if (value === "unavailable") return "尚無結果";
  return value ?? "尚無結果";
}

function displayExecutionOutcome(value?: string): string {
  if (value === "invalid_output") return "invalid_output · M6 contract";
  if (value === "decision_infeasible") return "decision_infeasible · M6 allocation";
  if (value === "unavailable") return "unavailable · no terminal outcome";
  return "available · terminal outcomes";
}

function metricLabel(metric: string): string {
  return metrics.find((item) => item.id === metric)?.label ?? metric;
}

function DownloadButtons({ run }: { run: RunSummary }) {
  const preferred = [
    "M9/decoupled_2_stage_rule_source_comparison_all_tables.csv",
    "M9/decoupled_2_stage_rule_source_comparison_all_tables.md",
    "M9/decoupled_2_stage_rule_source_comparison_all_tables.xlsx",
    "M9/decoupled_2_stage_rule_source_comparison_all_tables.zip",
    "M9/decoupled_2_stage_all_tables.csv",
    "M9/decoupled_2_stage_all_tables.md",
    "M9/decoupled_2_stage_all_tables.xlsx",
    "M9/decoupled_2_stage_all_tables.zip",
    "M9/insight_report.md",
    "M9/insight_summary.json",
    "M4/scenario_generation_diagnostics.json",
    "M4/scenario_feasibility_report.json"
  ];
  const artifacts = preferred
    .map((path) => run.artifacts.find((artifact) => artifact.path === path))
    .filter((artifact): artifact is Artifact => Boolean(artifact));
  return (
    <Stack direction="row" spacing={1} flexWrap="wrap">
      {artifacts.map((artifact) => (
        <Button
          key={artifact.path}
          variant="outlined"
          size="small"
          startIcon={<DownloadIcon />}
          href={`${API_BASE}/decoupled-2-stage-experiment/runs/${run.run_id}/files/${artifact.path}`}
        >
          {downloadArtifactLabel(artifact.path)}
        </Button>
      ))}
    </Stack>
  );
}

function downloadArtifactLabel(path: string): string {
  if (path.endsWith("/insight_report.md")) return "Insight report";
  if (path.endsWith("/insight_summary.json")) return "Insight summary";
  const extension = path.split(".").pop()?.toUpperCase() ?? "FILE";
  if (path.includes("rule_source_comparison_all_tables")) return `Comparison ${extension}`;
  if (path.includes("all_tables")) return `All tables ${extension}`;
  return extension;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const detail = await response.text();
    let message = detail || `HTTP ${response.status}`;
    let payload: ExperimentFailurePayload | undefined;
    try {
      const parsed = JSON.parse(detail) as { detail?: unknown };
      if (parsed.detail && typeof parsed.detail === "object") {
        payload = parsed.detail as ExperimentFailurePayload;
        message = payload.message || message;
      } else if (typeof parsed.detail === "string") {
        message = parsed.detail;
      }
    } catch {
      // Preserve plain-text API errors.
    }
    const error = new Error(message) as ApiError;
    error.payload = payload;
    throw error;
  }
  return response.json() as Promise<T>;
}

function getExperimentFailurePayload(error: unknown): ExperimentFailurePayload | null {
  if (!error || typeof error !== "object" || !("payload" in error)) return null;
  const payload = (error as ApiError).payload;
  return payload?.message ? payload : null;
}

function formatMetric(value: unknown): string {
  if (value === null || value === undefined || value === "") return "unavailable";
  if (typeof value === "number") return value.toFixed(3);
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(3) : String(value);
}

function formatExperimentError(error: unknown): string {
  const raw = error instanceof Error ? error.message : "實驗執行失敗";
  if (raw.includes("M4 decision feasibility preflight failed")) {
    return `Run 在 M4 feasibility preflight 停止：${raw}。此 scenario 未產生正式 M8/M9 結果，請先檢查 topology target capacity 或 scenario policy。`;
  }
  if (raw.includes("M4 could not generate a feasible scenario")) {
    return `Run 在 M4 scenario generation 停止：${raw}。在最大候選嘗試次數內沒有找到可行正式 scenario，未產生正式 M8/M9 結果。`;
  }
  if (raw.includes("Ideal invariant failed")) {
    return `Run 在 M7 ideal invariant 停止：${raw}。M4 可行 scenario 未全部通過獨立 validator，因此未發布正式 M8/M9 結果。`;
  }
  return raw;
}

function formatCount(value: number | null | undefined): string {
  return typeof value === "number" ? value.toLocaleString() : "unavailable";
}

function formatRunDate(value: string | undefined): string {
  if (!value) return "時間未知";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

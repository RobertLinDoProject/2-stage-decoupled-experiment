# Decoupled 2-Stage Crowd Evacuation Experiment System

## Purpose

This repository contains the runnable implementation of a two-stage crowd
evacuation experiment system. Its purpose is to measure how empirical
perception uncertainty affects evacuation decisions when the decision model
receives observed population counts instead of ground-truth counts.

The system connects empirical perception residuals to a capacity-aware
evacuation decision process while keeping M7 validation independent from the
decision input. This makes the `w/o` and `w/` comparison reproducible and
auditable rather than allowing the decision result to validate itself.

## Research question and intended evidence

The experiment is designed to establish whether perception error changes the
reliability of an evacuation decision system under otherwise matched
conditions. It compares two paired branches using the same scenario,
topology, capacity, rule source, model, regime and trial seed:

- `w/o Two-stage framework`: M6 receives the ground-truth population.
- `w/ Two-stage framework`: M6 receives the M5 population observation with
  empirical perception residuals.

The final Run produces evidence for three questions:

1. Does the perception-affected deployment branch have lower validated
   reliability than the ideal branch?
2. How large is the reliability change, and is its direction consistent across
   the selected topology, perception model, density regime, rule source and
   decision interface?
3. Which trials fail M7, and are the failures associated with the observed
   population error, infeasible evacuation allocation, invalid model output or
   another recorded execution outcome?

The primary reported values are:

```text
R_ideal  = ideal valid trials / completed ideal trials
R_deploy = deployment valid trials / completed deployment trials
Delta_R  = R_ideal - R_deploy
```

`Delta_R > 0` indicates a reliability decrease in the perception-affected
branch for the selected Run. The result supports a conclusion about the
selected configurations and paired trials; it is not by itself a universal
claim about every topology, perception model, population density or GAI model.
M8 stores the aggregate evidence, while M9 stores the delivery and
reproducibility manifests needed to audit how the conclusion was produced.

## Key features

- **Decoupled two-stage comparison:** compares a ground-truth decision input
  (`w/o Two-stage framework`) with a perception-affected observation
  (`w/ Two-stage framework`).
- **Empirical perception errors:** samples residuals from the formal
  perception input tables instead of using an arbitrary synthetic noise rule.
- **Capacity-aware evacuation decisions:** combines topology, capacity and
  scenario ground truth before the decision stage.
- **Independent validation:** M7 validates both branches with the same human
  gold-standard validator; M8 then calculates the formal reliability metrics.
- **Reproducible delivery:** fixed seeds, paired trials, input checksums and
  M9 manifests preserve the evidence needed to inspect or reproduce a Run.
- **Model comparison:** supports deterministic `rule-based` decisions and
  compares a local Ollama model with an OpenAI model through the same M6/M7
  experiment pipeline.

## Execution pipeline

The executable pipeline is:

```text
formal perception data
  -> M1 canonical samples / M2 empirical residuals
  -> M3 topology and capacity
  -> M4 scenario ground truth
  -> M5 observed population
  -> M6 evacuation decision
  -> M7 independent validation
  -> M8 aggregate metrics
  -> M9 delivery and reproducibility manifests
```

The main paired comparison is:

```text
w/o Two-stage framework: M6 sees scenario_gt
w/  Two-stage framework: M6 sees the M5 observation with empirical residuals
```

Both branches use the same scenario, topology, capacity and human
gold-standard M7 validator. The primary reliability values are read from M8;
their interpretation and intended evidence are described in the research
question section above.

## Repository layout

```text
backend/                 FastAPI service and M0-M9 application code
frontend/                React UI and standalone topology preview
configs/                 Runtime and experiment configuration
data/Perception資料/     Formal A1-A3 perception input tables
data/Topology資料/       Human and AI-generated topology packages
scripts/                 Preflight, packaging and verification scripts
 storage/                 Local runtime output directory
```

Historical runs are intentionally not committed to Git. They remain on the
machine that executes the experiment under `storage/published/runs/`.

## Requirements

- Docker Desktop with Docker Compose
- Python 3.12 or newer for host-side tests and scripts
- Node.js 22.13 or newer and pnpm 11 for frontend development, or Docker for the full build
- Optional M6 model comparison: a local Ollama model and/or OpenAI model access

## Quick start with Docker

From the repository root:

```powershell
Copy-Item .env.example .env
# Rule-based Runs need no additional .env changes.
# Configure .env only if live GAI model comparison is required.
docker compose up -d --build
```

Confirm that the containers are healthy before opening the UI:

```powershell
docker compose ps
```

Useful endpoints:

- Frontend: `http://localhost:5273`
- API documentation: `http://localhost:8000/docs`
- API health: `http://localhost:8000/api/v1/health`
- API readiness: `http://localhost:8000/api/v1/ready`

The frontend is served by the `frontend` container and the API reads local
input data and writes Run artifacts to the mounted `storage/` directory.

Stop the local stack when it is no longer needed:

```powershell
docker compose down
```

## Host virtual environment

The host environment is only for tests and utility scripts; application
services still run in Docker.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_host_venv.ps1 -InstallDev
.\.venv\Scripts\python.exe -m unittest discover backend/src/two_stage/tests
```

If PowerShell execution policy blocks activation, use the venv interpreter by
full path as shown above. Activation is optional.

## GAI Model Comparison: Local Ollama Model vs. OpenAI Model

GAI is the M6 decision interface used to compare a local Ollama model with an
OpenAI model. Both model paths must produce the same canonical action format;
M7 validates each action independently under the same experiment conditions.
Do not put keys in the frontend, Git, Run artifacts or manifests.

### Choose a run mode

For a local smoke test or a formal `rule-based` run, select only `rule-based`
under **Decision interface** in the frontend. No GAI provider is called, and
no environment-variable change is required for this frontend workflow.

The frontend sends the selected interface and model as part of the Run request;
it does not edit `.env` or change the API container environment.

### Advanced server-side GAI switches

These variables are read by the backend when the API container starts. They are
not frontend controls:

- `LIVE_GAI_PROVIDER_ENABLED=false` prevents live external GAI requests.
- `GAI_EXECUTION_MODE=reserved_unavailable` keeps the GAI interface reserved
  but unavailable; it does not select `rule-based` in the frontend.

Only change these values in the local `.env` when you want to change the
backend-wide GAI policy. Restart or recreate the API container after changing
them. For normal `rule-based` operation, simply select `rule-based` in the
frontend.

```dotenv
LIVE_GAI_PROVIDER_ENABLED=false
GAI_EXECUTION_MODE=reserved_unavailable
```

For a live GAI run, configure the backend with
`LIVE_GAI_PROVIDER_ENABLED=true`, keep
`GAI_EXECUTION_MODE=live`, configure the local Ollama model or OpenAI model
below, and select `gai` in the frontend. The frontend preflight checks model
availability, configuration and the estimated action-call budget before
creating a Run.

### Local Ollama Model

Install Ollama on the host, pull the configured model, and use settings similar
to the following. Docker reaches the host-side Ollama service through
`host.docker.internal`:

```powershell
ollama pull mistral:7b-instruct-v0.3-q4_K_M
```

```dotenv
LIVE_GAI_PROVIDER_ENABLED=true
GAI_EXECUTION_MODE=live
GAI_PROVIDER_NAME=ollama
GAI_PROVIDER_ENDPOINT=http://host.docker.internal:11434/api/chat
GAI_PROVIDER_MODEL=mistral:7b-instruct-v0.3-q4_K_M
GAI_PROMPT_TEMPLATE_VERSION=m6_ollama_action_v1
GAI_BUDGET_MODE=auto
GAI_BUDGET_HARD_LIMIT=50000
```

### OpenAI Model

OpenAI is configured server-side in `.env` and is never sent to the
frontend:

```dotenv
OPENAI_API_KEY=replace-with-a-local-secret
OPENAI_API_ENDPOINT=https://api.openai.com/v1/responses
OPENAI_MODEL=gpt-5-nano-2025-08-07
```

In the frontend, choose `gai` and then select the `OpenAI` model option. The
API key remains server-side and is never sent to the frontend.

The provider preflight is automatically triggered when the frontend Run button
is clicked. A provider quota interruption preserves completed paired trials and
makes the Run resumable; it does not turn unfinished calls into valid or invalid
trials.

## Running experiments

### End-to-end UI flow

1. Open `http://localhost:5273` after the containers are healthy.
2. In **Run Settings**, select the rule source set, decision interface,
   topologies, perception models and density regimes.
3. Set `root seed`, `trials / condition`, `scenarios / regime` and
   `Risk Consistency β`. The same root seed, input data and configuration can
   reproduce the same scenario and residual samples.
4. If `gai` is selected, choose the `Local Ollama · Mistral 7B` model or the
   `OpenAI · GPT-5 Nano` model. Make sure the selected model passes preflight
   before starting a large Run.
5. Click `執行實驗` (`Run experiment`). This automatically runs the preflight
   checks; you do not need to call the preflight API manually. The system checks
   the input selection, scenario feasibility, provider availability and budget
   before creating the background Run. If any check fails, no Run is created.
6. Monitor the Run status in the UI. Completed Runs can be loaded from the
   Run history and inspected in the comparison tables, selected configuration
   detail, Paper View and topology preview.
7. Use the download actions in the result views, or inspect the complete Run
   directory under `storage/published/runs/<run_id>/`.

For a first local smoke test, use `rule-based`, one topology, one perception
model, one regime, a small scenario count and `trials / condition = 1`. This
keeps the run deterministic and avoids external provider calls.

Before creating a Run, the system checks input pools, scenario reproducibility,
provider availability and the estimated GAI action-call budget.

### Frontend functional test settings

The following values are a reference configuration for testing the frontend
Run Settings, automatic preflight flow and result presentation. Select the
equivalent values in the frontend. This is not an API payload that users need
to submit manually:

- **Run purpose:** `exploratory`
- **root seed:** `114`
- **scenarios / regime:** `8`
- **trials / condition:** `1`
- **Rule source set:** `human_manual_v1`
- **Decision interface:** `rule_based`
- **Topologies:** `fcu`
- **Perception models:** `csrnet_den_v1`
- **Regimes:** `LOW`

## Run results and portability

The repository does not contain historical Run results. A completed Run is
stored locally at:

```text
storage/published/runs/<run_id>/
```

To inspect a Run on another machine, copy the complete `<run_id>` directory
into the same path, keep its source input data checksums unchanged, and restart
the API/frontend containers if they are already running. Never copy `.env`,
API keys or `.venv` as part of a result bundle.

Portable packaging and GPU-host setup are documented in:

- `scripts/package_portable_runtime.ps1`
- `scripts/verify_portable_environment.ps1`

The package contains source code, configuration and formal input data.
Generated ZIP files, node modules, virtual environments and historical results
remain local.

## Verification

```powershell
docker compose build
docker compose up -d
docker compose ps
Invoke-WebRequest http://localhost:8000/api/v1/health

.\.venv\Scripts\python.exe -m unittest discover backend/src/two_stage/tests
```

For frontend development:

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm typecheck
pnpm build
```

Tests and builds do not call an external GAI provider. Provider tests use fake
transports where applicable.

The commands above verify the local runtime and build. They do not replace a
formal experiment Run; create a Run from the frontend after the health and
provider preflight checks pass.

## License

See `LICENSE`.

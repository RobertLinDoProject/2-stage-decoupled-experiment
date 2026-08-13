# Decoupled 2-Stage Experiment

This repository contains the runnable implementation of a two-stage crowd
evacuation experiment system. It connects empirical perception residuals to a
capacity-aware evacuation decision process while keeping M7 validation
independent from the decision input.

## What the system does

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
gold-standard M7 validator. The primary reliability values are read from M8:

```text
R_ideal  = ideal valid trials / completed ideal trials
R_deploy = deployment valid trials / completed deployment trials
Delta_R  = R_ideal - R_deploy
```

## Repository layout

```text
backend/                 FastAPI service and M0-M9 application code
frontend/                React UI and standalone topology preview
configs/                 Runtime and experiment configuration
Data/Perception資料/     Formal A1-A3 perception input tables
Data/Topology資料/       Human and AI-generated topology packages
scripts/                 Preflight, packaging and verification scripts
 storage/                 Local runtime output directory
```

Historical runs are intentionally not committed to Git. They remain on the
machine that executes the experiment under `storage/published/runs/`.

## Requirements

- Docker Desktop with Docker Compose
- Python 3.12 or newer for host-side tests and scripts
- Node.js 22.13 or newer and pnpm 11 for frontend development, or Docker for the full build
- Optional M6 GAI provider: Ollama on the host or OpenAI API access

## Quick start with Docker

From the repository root:

```powershell
Copy-Item .env.example .env
# Edit .env and choose the desired provider settings.
docker compose up -d --build
```

Useful endpoints:

- Frontend: `http://localhost:5273`
- API documentation: `http://localhost:8000/docs`
- API health: `http://localhost:8000/health`

The frontend is served by the `frontend` container and the API reads local
input data and writes Run artifacts to the mounted `storage/` directory.

## Host virtual environment

The host environment is only for tests and utility scripts; application
services still run in Docker.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_host_venv.ps1 -InstallDev
.\.venv\Scripts\python.exe -m unittest discover backend/src/two_stage/tests
```

If PowerShell execution policy blocks activation, use the venv interpreter by
full path as shown above. Activation is optional.

## GAI providers

GAI is an M6 decision interface. It must produce the canonical action format;
M7 still validates the action independently. Do not put keys in the frontend,
Git, Run artifacts or manifests.

### Local Ollama

Use settings similar to:

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

### OpenAI

OpenAI is configured server-side in `.env` and is never sent to the
frontend:

```dotenv
OPENAI_API_KEY=replace-with-a-local-secret
OPENAI_API_ENDPOINT=https://api.openai.com/v1/responses
OPENAI_MODEL=gpt-5-nano-2025-08-07
```

Run provider preflight and a small smoke test before a large GAI run. A
provider quota interruption preserves completed paired trials and makes the
Run resumable; it does not turn unfinished calls into valid or invalid trials.

## Running experiments

Use the frontend Run Settings to select topology, perception model, regime,
rule source, trial count and decision method. Before creating a Run, the
system checks input pools, scenario reproducibility, provider availability and
the estimated GAI action-call budget.

For a small API smoke request, use the API documentation as the source of the
current request schema. A typical configuration includes:

```json
{
  "run_purpose": "exploratory",
  "root_seed": 114,
  "scenarios_per_regime": 8,
  "trial_count_per_condition": 1,
  "selected_rule_sources": ["human_manual_v1"],
  "selected_interfaces": ["rule_based"],
  "selected_topology_ids": ["fcu"],
  "selected_model_ids": ["csrnet_den_v1"],
  "selected_regimes": ["LOW"]
}
```

The exact endpoint and schema can evolve with the API version; use the
OpenAPI page rather than copying an old payload blindly.

## Run results and portability

The repository does not contain historical Run results. A completed Run is
stored locally at:

```text
storage/published/runs/<run_id>/
```

To inspect a Run on another machine, copy the complete `<run_id>` directory
into the same path, keep its source input Data checksums unchanged, and restart
the API/frontend containers if they are already running. Never copy `.env`,
API keys or `.venv` as part of a result bundle.

Portable packaging and GPU-host setup are documented in:

- `portable_gpu_run_guide.md`
- `scripts/package_portable_runtime.ps1`
- `scripts/verify_portable_environment.ps1`

The package contains source code, configuration, contracts and formal input
data. Generated ZIP files, node modules, virtual environments and historical
results remain local.

## Verification

```powershell
docker compose build
docker compose up -d
docker compose ps

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

## License

See `LICENSE`.

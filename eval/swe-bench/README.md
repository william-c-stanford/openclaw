# SWE-bench Mini Evaluation Wrapper for OpenClaw

This directory contains a lightweight, production-oriented wrapper for running **SWE-bench mini** evaluations with `openclaw`.

It automates the full loop:

1. Load SWE-bench mini instances from Hugging Face (`MariusHobbhahn/swe-bench-verified-mini`)
2. For each instance:
   - Clone the target GitHub repo
   - Checkout the instance `base_commit`
   - Run `openclaw agent --json` on the issue statement
   - Extract a patch (git diff)
   - Write predictions to `predictions.jsonl`
3. Run `swebench.harness.run_evaluation` to score results

---

## Prerequisites

- Python **3.11+**
- Docker installed and running (required by SWE-bench harness)
- `openclaw` CLI installed and configured
- Network access for:
  - Hugging Face dataset download
  - GitHub clone operations
  - Any model/provider access required by your `openclaw` setup

---

## Setup

From repo root:

```bash
bash eval/swe-bench/setup.sh
```

This script will:

- verify `python3`/`pip3`
- create `eval/swe-bench/.venv` (if missing)
- install `requirements.txt`
- verify Docker is available and running
- verify `openclaw` is available in `PATH`

Manual setup alternative:

```bash
python3 -m venv eval/swe-bench/.venv
source eval/swe-bench/.venv/bin/activate
pip install -r eval/swe-bench/requirements.txt
```

---

## Basic Usage

```bash
source eval/swe-bench/.venv/bin/activate
python eval/swe-bench/run.py
```

This runs inference and evaluation on the default dataset:

- dataset: `MariusHobbhahn/swe-bench-verified-mini`
- split: `test` (50 instances)
- workers: `4`
- thinking: `low`

### Quick end-to-end test (1 instance + scoring)

```bash
eval/swe-bench/.venv/bin/python eval/swe-bench/run.py --instances 1 --workers 1 --thinking low
```

This command runs inference on one instance and then runs the SWE-bench scorer.

---

## Advanced Flags
```bash
python eval/swe-bench/run.py [options]
```

Key options:

- `--instances N`  
  Limit the number of instances processed.

- `--workers N`  
  Number of parallel inference workers.

- `--dataset DATASET_NAME`  
  Override dataset source (default mini dataset above).

- `--run-id RUN_ID`  
  Set a deterministic run identifier for artifact paths.

- `--skip-eval`  
  Run inference only, skip swebench harness scoring.

- `--dry-run`  
  Load/filter instances and print run info without executing inference/eval.

- `--instance-filter GLOB`  
  Filter instances by `instance_id` glob pattern (for example `"sympy__sympy-*"`).

Additional useful options:

- `--thinking {low,medium,high}`
- `--agent AGENT_NAME` (pass through to `openclaw agent --agent ...`)
- `--local` / `--gateway` (choose openclaw execution mode)
- `--timeout SECONDS` (per-instance timeout)
- `--model-name NAME` (written into prediction rows)

---

## Output Artifacts

Per run:

- Predictions JSONL:  
  `eval/swe-bench/results/<run_id>/predictions.jsonl`

- SWE-bench evaluation artifacts:  
  `evaluation_results/<run_id>/...`

Each predictions row follows SWE-bench format:

```json
{"instance_id":"...","model_name_or_path":"openclaw","model_patch":"diff --git ..."}
```

---

## Metrics You Get Back

The harness/wrapper will report:

- total instances evaluated
- resolved count
- resolved percentage (`resolved / total * 100`)
- per-instance status table (`instance_id | resolved | patch_applied` when available in artifacts)

Cost notes:

- The wrapper does not directly compute provider billing totals.
- You can estimate cost from your OpenClaw/model provider logs and multiply by run volume.
- If your OpenClaw configuration emits token/cost telemetry, you can aggregate that externally by `run_id`.

---

## How It Works (Architecture)

- `config.py`  
  Dataclass + argparse config surface.

- `prompts.py`  
  Task prompt templates + robust patch extraction (`<patch>...</patch>` first, fallback to `diff --git`).

- `agent_driver.py`  
  Per-instance runner:
  - clone repo at `base_commit`
  - invoke `openclaw agent --json`
  - parse JSONL/single-JSON stdout safely
  - extract patch
  - fallback follow-up patch request
  - final fallback to `git diff HEAD`

- `inference.py`  
  Threaded inference orchestration + prediction file writing.

- `evaluate.py`  
  Launches `python -m swebench.harness.run_evaluation`, streams logs, summarizes results.

- `run.py`  
  Single entrypoint for dataset load → inference → optional evaluation.

---

## Using Different OpenClaw Agents/Plugins

Use `--agent` to route requests to a specific OpenClaw agent/plugin profile:

```bash
python eval/swe-bench/run.py --agent your-agent-id
```

This is passed directly to:

```bash
openclaw agent --agent your-agent-id ...
```

You can combine with thinking and mode options:

```bash
python eval/swe-bench/run.py --agent your-agent-id --thinking medium --gateway
```

---

## Examples

Run only SymPy-related instances:

```bash
python eval/swe-bench/run.py --instance-filter "sympy__sympy-*"
```

Run first 10 instances with 2 workers:

```bash
python eval/swe-bench/run.py --instances 10 --workers 2
```

Inference-only smoke check:

```bash
python eval/swe-bench/run.py --instances 2 --skip-eval
```

Plan run without execution:

```bash
python eval/swe-bench/run.py --dry-run --instances 5
```

---

## Running Tests

From repo root:

```bash
eval/swe-bench/.venv/bin/pytest eval/swe-bench/tests/ -v
```

## See Also

- `eval/README.md`
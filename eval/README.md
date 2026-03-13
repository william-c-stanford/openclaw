# Evaluation Framework

This directory is a self-contained evaluation framework for benchmarking OpenClaw agent capabilities against standard software engineering benchmarks.

## Philosophy

- Each benchmark lives in its own subdirectory.
- Benchmark code does not touch the main codebase.
- Benchmarks talk to OpenClaw purely through the CLI (`openclaw agent --local --json`).

## Motivation

OpenClaw has a large and growing plugin ecosystem. Those plugins influence how the agent reasons, retrieves context, and responds.

Beyond plugins, there are many scaffolding improvements and core features under consideration. Each change can shift behavior in ways that are not obvious from local testing.

Without a standard evaluation harness, it is hard to tell whether a proposal improves agent performance or introduces regressions. Intuition and anecdotal wins are not enough for reliable decisions.

This eval framework makes the loop concrete: run a benchmark before and after a change, compare the numbers, and decide with evidence. This is especially important as the plugin surface grows, because a plugin that improves conversational quality might silently reduce code-repair accuracy unless you benchmark it.

## Current benchmarks
| Benchmark | Directory | What it tests | Dataset size |
| --- | --- | --- | --- |
| SWE-bench Mini | `eval/swe-bench/` | Code repair on real GitHub issues | 50 instances |

## Results

When contributing benchmark results, always record the OpenClaw plugins used, the model, the benchmark, and the performance score.

| OpenClaw Plugins | Model | Benchmark | Performance | Avg Tokens/Instance |
| --- | --- | --- | --- | --- |
| — | openai-codex/gpt-5.3-codex | swe-bench-mini | x *(run in progress)* | — |

When submitting benchmark results, fill in all five columns. Use — for plugins if running with the default configuration.

Avg tokens per instance is calculated from input + output tokens across all instances in the run. Cache tokens are tracked separately in token_usage.json.
## Run any benchmark
1. `cd` to the benchmark directory
2. Activate its virtual environment
3. Run `python run.py`

Example shape:

```bash
cd eval/swe-bench
source .venv/bin/activate
python run.py
```

## Shared utilities (`eval/utils/`)

The `eval/utils/` directory contains Python modules shared across all benchmarks.
Add benchmark-agnostic helpers here so every new benchmark can import them without
copying code.

### `setup_inspector` — OpenClaw environment probe + binary check

`eval/utils/setup_inspector.py` captures the current OpenClaw setup before a
benchmark run: installed plugins/extensions, available skills, and configured model.
It probes three CLI commands concurrently (10-second timeout each) and degrades
gracefully on any failure. It also provides `verify_openclaw()` — the hard
prerequisite check every benchmark should call before inference.

**Import in your `run.py`:**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from setup_inspector import (
    SetupSnapshot, capture_setup_snapshot, sanitize_for_display, verify_openclaw
)
```

**Check the binary and capture the snapshot:**

```python
import asyncio

oclaw_version = verify_openclaw()   # raises RuntimeError if not found
snapshot = asyncio.run(capture_setup_snapshot(
    run_id=config.run_id,
    thinking=config.thinking_level,
    agent=config.openclaw_agent,
    openclaw_version=oclaw_version,
))
```

**Capture and save the snapshot:**

```python
import asyncio, json

snapshot = asyncio.run(capture_setup_snapshot(
    run_id=config.run_id,
    thinking=config.thinking_level,
    agent=config.openclaw_agent,
    openclaw_version=oclaw_version,   # from --version check
))
# Write to results dir (skip during --dry-run)
snapshot_path = Path(config.output_dir) / config.run_id / "setup_snapshot.json"
snapshot_path.parent.mkdir(parents=True, exist_ok=True)
snapshot_path.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

**Output artifact — `results/<run_id>/setup_snapshot.json`:**

```json
{
  "schema_version": 1,
  "run_id": "swe-mini-20260313-184033",
  "openclaw_version": "2026.3.1",
  "model": "anthropic/claude-opus-4-6",
  "thinking": "low",
  "agent": null,
  "extensions": [{"id": "msteams", "name": "Microsoft Teams"}],
  "skills": [{"name": "code-review", "eligible": true}],
  "python_version": "3.12.0",
  "platform_info": "macOS-14.4-arm64",
  "captured_at": "2026-03-13T18:40:33+00:00"
}
```

The snapshot uses `sort_keys=True` so `git diff` comparisons between runs are stable.
It is schema-versioned (`schema_version: 1`); readers should tolerate unknown keys
for forward compatibility.

### `token_usage` — token usage tracking

`eval/utils/token_usage.py` accumulates per-instance token counts during inference
and persists them as `token_usage.json` in the results directory. It provides a
`TokenUsage` frozen dataclass (JSON-serialisable, schema-versioned) and a mutable
`TokenUsageAccumulator` for collecting counts across concurrent inference results.

**Import in your inference module:**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from token_usage import TokenUsage, TokenUsageAccumulator
```

**Accumulate and save:**

```python
accumulator = TokenUsageAccumulator()

# ... inside your results loop:
if result is not None:
    accumulator.add(
        result.input_tokens, result.output_tokens,
        result.cache_read_tokens, result.cache_write_tokens,
    )

# ... at end of inference:
token_usage = accumulator.save(
    path=output_dir / "token_usage.json",
    run_id=config.run_id,
    instances_total=len(instances),
)
```

**Output artifact — `results/<run_id>/token_usage.json`:**

```json
{
  "schema_version": 1,
  "run_id": "swe-mini-20260313-184033",
  "generated_at": "2026-03-13T18:40:55+00:00",
  "instances_total": 50,
  "instances_run": 48,
  "input_tokens": 1234567,
  "output_tokens": 98765,
  "cache_read_tokens": 45000,
  "cache_write_tokens": 0,
  "total_tokens": 1333332,
  "avg_tokens_per_instance": 27777
}
```

`total_tokens` is `input_tokens + output_tokens`; cache tokens are tracked
separately (matching API billing conventions). `avg_tokens_per_instance` is
integer division, 0 when no instances completed.

## Add a new benchmark

1. Create `eval/<name>/` with:
   - `setup.sh`
   - `requirements.txt`
   - `README.md`
   - `run.py`
2. Follow the SWE-bench pattern for flags:
   - `--skip-eval`
   - `--instances N`
   - `--run-id`
   - `--dry-run`
3. Import shared utilities from `eval/utils/` (see above).
4. Send tasks to OpenClaw via:
   - `openclaw agent --local --json --session-id <id> --message "<prompt>"`
5. Parse JSON response:
   - top-level `payloads` array
   - each item has a `"text"` field
6. Output results in whatever format your benchmark expects.
7. Add your benchmark entry to the table in this README.

## OpenClaw agent JSON interface

Expected output shape:

```json
{"payloads": [{"text": "..."}], "meta": {...}}
```

## Common prerequisites

- `openclaw` installed
- `openclaw` configured with a model provider

## Gitignore notes

The following are excluded from version control:

- `results/`
- `evaluation_results/`
- `__pycache__/`
- `.venv/`

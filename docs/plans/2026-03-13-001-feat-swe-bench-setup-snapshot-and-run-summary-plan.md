---
title: "feat: SWE-bench Setup Snapshot & Run Summary"
type: feat
status: active
date: 2026-03-13
deepened: 2026-03-13
---

# feat: SWE-bench Setup Snapshot & Run Summary

## Enhancement Summary

**Deepened on:** 2026-03-13
**Research agents used:** best-practices-researcher, architecture-strategist, kieran-python-reviewer,
performance-oracle, security-sentinel, code-simplicity-reviewer, spec-flow-analyzer,
pattern-recognition-specialist

### Key Improvements Over Initial Plan

1. **Terminology fix**: Use "extensions" not "plugins" — matches `extensions/*` repo convention
2. **Single model source**: Remove `actual_model` threading through `InstanceResult`; use `openclaw models list --json` as the one authoritative source (simpler, more reliable)
3. **Terminal output style**: Drop Unicode box-drawing in favour of `=== Header ===` / plain `print()` style that matches `inference.py` and `evaluate.py` exactly
4. **Parallelise subprocess calls**: Run all three CLI calls concurrently (asyncio.gather) — worst-case 10s not 30s
5. **Typed snapshot object**: Use a `@dataclass` for `SetupSnapshot` instead of raw `dict` for typed contract between `setup_inspector.py` and `run.py`
6. **Process cleanup**: Use `asyncio.create_subprocess_exec` + kill-on-timeout to prevent zombie processes
7. **Security**: Apply `_sanitize_session_id` pattern to `--run-id`; strip non-printable chars before printing any CLI-sourced strings
8. **Schema versioning**: Add `schema_version: 1` envelope to `setup_snapshot.json` and `token_usage.json`
9. **`--skip-eval` clarity**: Summary shows "N/A — evaluation skipped" not "0%" for resolved

### New Considerations Discovered

- Results directory may not exist when snapshot writes; writer must call `mkdir(parents=True, exist_ok=True)` itself
- `--skip-eval` and `--dry-run` need explicit snapshot behaviour definitions
- All-instances-fail path produces a misleading `FileNotFoundError`; add guard
- Plain-text fallback for `openclaw skills list` is fragile; return `[]` + warning instead
- `warnings.warn` is wrong for operational failures; use `logging` module throughout

---

## Overview

Enhance the SWE-bench evaluation harness (`eval/swe-bench/`) to automatically capture the
OpenClaw setup before each run (installed extensions, skills, and model) and display a rich
final summary after every run. This gives users a clear picture of how different configurations
affect benchmark performance.

## Problem Statement / Motivation

The eval harness already records token usage and SWE-bench resolve rates, but the output gives
no context about *why* one run might perform differently from another. Users cannot tell:

- Which OpenClaw extensions were active during the run
- Which skills were available to the agent
- Which model (and provider) was actually used
- How thinking level correlates with resolution rate and token cost

Without this context, comparing runs is guesswork. Adding a setup snapshot (saved to disk) and
a clear final summary makes every run self-documenting and comparable.

---

## Proposed Solution

### New file: `eval/swe-bench/setup_inspector.py`

A standalone module following the shape of `_verify_openclaw()` in `run.py`. Exposes a typed
`SetupSnapshot` dataclass and three private helper functions.

```python
# eval/swe-bench/setup_inspector.py
from __future__ import annotations

import asyncio
import json
import logging
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT = 10  # seconds per CLI call

@dataclass(frozen=True)
class SetupSnapshot:
    """Pre-run environment state for a SWE-bench eval run."""
    schema_version: int
    run_id: str
    openclaw_version: str
    model: str            # from `openclaw models list --json`
    thinking: str
    agent: str | None
    extensions: list[dict[str, Any]]   # from `openclaw extensions list --json`
    skills: list[dict[str, Any]]       # from `openclaw skills list --json`
    python_version: str
    platform: str
    captured_at: str      # ISO-8601 UTC timestamp

    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)
```

**Three private helpers** (all called concurrently via `asyncio.gather`):

```python
async def _get_extensions() -> list[dict[str, Any]]:
    """Run `openclaw extensions list --json`; return [] on any failure."""

async def _get_skills() -> list[dict[str, Any]]:
    """Run `openclaw skills list --json`; return [] on any failure.
    Note: no plain-text fallback — degrade gracefully to empty list."""

async def _get_model() -> str:
    """Run `openclaw models list --json`; return 'unknown' on any failure."""
```

**Public entry point:**

```python
async def capture_setup_snapshot(config: EvalConfig) -> SetupSnapshot:
    """Capture all three CLI values in parallel, assemble and return snapshot."""
    extensions, skills, model = await asyncio.gather(
        _get_extensions(),
        _get_skills(),
        _get_model(),
    )
    return SetupSnapshot(
        schema_version=1,
        run_id=config.run_id,
        openclaw_version=_get_openclaw_version(),
        model=model,
        thinking=config.thinking_level,
        agent=config.openclaw_agent,
        extensions=extensions,
        skills=skills,
        python_version=sys.version,
        platform=platform.platform(),
        captured_at=datetime.now(timezone.utc).isoformat(),
    )
```

**Each helper follows this pattern (no zombies, no silent failures):**

```python
async def _get_extensions() -> list[dict[str, Any]]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "openclaw", "extensions", "list", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=SUBPROCESS_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.warning("openclaw extensions list timed out after %ds", SUBPROCESS_TIMEOUT)
            return []
        if proc.returncode != 0:
            logger.warning("openclaw extensions list exited %d", proc.returncode)
            return []
        data = json.loads(stdout.decode("utf-8", errors="replace"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        logger.debug("get_extensions failed", exc_info=True)
        return []
```

### No changes to `eval/swe-bench/agent_driver.py`

**Simplification decision (from code-simplicity-reviewer):** The initial plan proposed adding
`model: str | None` to `InstanceResult` to capture the actual model from the agent JSON
response. This was dropped because:

- `openclaw models list --json` is a more reliable and simpler source for model identity
- Harvesting model from the first successful `InstanceResult` in `as_completed()` order is
  non-deterministic and fragile
- `InstanceResult` is a write-once result object; threading model identity through it couples
  two unrelated concerns

`agent_driver.py` is unchanged.

### No changes to `eval/swe-bench/inference.py`

No `actual_model` field needed. `inference.py` remains at its current scope.

### Changes to `eval/swe-bench/run.py`

**Add `--run-id` sanitization** (security fix — path traversal risk):

```python
# Apply same pattern as _sanitize_session_id in agent_driver.py
import re
def _sanitize_run_id(run_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9\-_]", "", run_id)
    return clean[:120] or "run"
```

Apply to `Config.run_id` before it is used in path construction.

**Before inference, call snapshot and print setup header:**

```python
# After _verify_openclaw(), after _load_instances(), before InferenceRunner
snapshot = asyncio.run(capture_setup_snapshot(config))

# Write to disk — create dir independently since InferenceRunner hasn't run yet
snapshot_dir = Path(config.output_dir) / config.run_id
snapshot_dir.mkdir(parents=True, exist_ok=True)
snapshot_path = snapshot_dir / "setup_snapshot.json"
snapshot_path.write_text(
    json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

_print_setup_header(snapshot)
```

**`_print_setup_header` — plain print, matching existing style:**

```python
def _print_setup_header(snapshot: SetupSnapshot) -> None:
    """Print setup summary before inference begins."""
    print("=== OpenClaw Setup ===")
    print(f"[setup] Run ID       : {snapshot.run_id}")
    print(f"[setup] Model        : {snapshot.model}")
    print(f"[setup] Thinking     : {snapshot.thinking}")
    print(f"[setup] Agent        : {snapshot.agent or '(none)'}")
    ext_count = len(snapshot.extensions)
    print(f"[setup] Extensions   : {ext_count} installed")
    if ext_count and ext_count <= 10:
        for ext in snapshot.extensions:
            name = ext.get("name", ext.get("id", "?"))
            print(f"[setup]                • {_sanitize_for_display(name)}")
    elif ext_count > 10:
        for ext in snapshot.extensions[:5]:
            name = ext.get("name", ext.get("id", "?"))
            print(f"[setup]                • {_sanitize_for_display(name)}")
        print(f"[setup]                ... and {ext_count - 5} more")
    skill_count = len(snapshot.skills)
    print(f"[setup] Skills       : {skill_count} available")
```

**After evaluation, print full run summary:**

```python
def _print_run_summary(
    snapshot: SetupSnapshot,
    token_usage: dict[str, Any],
    eval_results: dict[str, Any] | None,
    skip_eval: bool,
) -> None:
    """Replace the existing minimal summary with a structured block."""
    print("\n=== SWE-bench Mini Run Summary ===")
    print(f"[summary] Run ID        : {snapshot.run_id}")
    print(f"[summary] Results       : {Path(config.output_dir) / config.run_id}")

    print("[summary] --- Setup ---")
    print(f"[summary] Model         : {snapshot.model}")
    print(f"[summary] Thinking      : {snapshot.thinking}")
    print(f"[summary] Agent         : {snapshot.agent or '(none)'}")
    print(f"[summary] Extensions    : {len(snapshot.extensions)}")
    print(f"[summary] Skills        : {len(snapshot.skills)}")

    print("[summary] --- Performance ---")
    if skip_eval:
        print("[summary] Resolved      : N/A — evaluation skipped (--skip-eval)")
    elif eval_results:
        total = eval_results.get("total", 0)
        resolved = eval_results.get("resolved", 0)
        pct = (resolved / total * 100) if total else 0.0
        print(f"[summary] Resolved      : {resolved} / {total}  ({pct:.1f}%)")
    else:
        print("[summary] Resolved      : N/A — evaluation data unavailable")

    print("[summary] --- Token Usage ---")
    inst_run = token_usage.get("instances_run", 0)
    print(f"[summary] Input         : {token_usage.get('input_tokens', 0):,}")
    print(f"[summary] Output        : {token_usage.get('output_tokens', 0):,}")
    print(f"[summary] Cache read    : {token_usage.get('cache_read_tokens', 0):,}")
    print(f"[summary] Cache write   : {token_usage.get('cache_write_tokens', 0):,}")
    print(f"[summary] Total         : {token_usage.get('total_tokens', 0):,}")
    if inst_run > 0:
        print(f"[summary] Avg / inst    : {token_usage.get('avg_tokens_per_instance', 0):,.0f}")
```

**Add ANSI/non-printable stripping helper:**

```python
import unicodedata

def _sanitize_for_display(value: str, max_len: int = 80) -> str:
    """Strip non-printable characters to prevent ANSI injection in terminal output."""
    clean = "".join(c for c in value if unicodedata.category(c)[0] != "C")
    return clean[:max_len]
```

**Add all-instances-fail guard (spec flow gap fix):**

```python
if token_usage.get("instances_run", 0) == 0 and not config.skip_eval:
    print("[warn] All instances failed inference — skipping evaluation to avoid misleading errors.")
    config = replace(config, skip_eval=True)
```

### Changes to `eval/swe-bench/token_usage.json` schema

Add `schema_version` envelope (additive, backward-compatible):

```json
{
  "schema_version": 1,
  "generated_at": "2026-03-13T00:00:00Z",
  "run_id": "swe-mini-20260313-000000",
  "instances_total": 50,
  "instances_run": 50,
  "input_tokens": 123456,
  "output_tokens": 234567,
  "cache_read_tokens": 0,
  "cache_write_tokens": 0,
  "total_tokens": 358023,
  "avg_tokens_per_instance": 7160
}
```

### New output artifact: `results/<run_id>/setup_snapshot.json`

```json
{
  "schema_version": 1,
  "run_id": "swe-mini-20260313-184033",
  "openclaw_version": "2026.3.1",
  "model": "anthropic/claude-opus-4-6",
  "thinking": "low",
  "agent": null,
  "extensions": [
    {"id": "msteams", "name": "Microsoft Teams"},
    {"id": "matrix",  "name": "Matrix"}
  ],
  "skills": [
    {"name": "code-review"},
    {"name": "swe-bench-patch"}
  ],
  "python_version": "3.12.0",
  "platform": "macOS-14.4-arm64",
  "captured_at": "2026-03-13T18:40:33.000000+00:00"
}
```

`sort_keys=True` in `json.dumps` ensures stable key ordering for `git diff` comparisons.

### New file: `eval/swe-bench/tests/test_setup_inspector.py`

Tests using `asyncio` + `unittest.mock.AsyncMock`:

```python
# Pattern for each test: patch at the import site, not subprocess globally
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import pytest
from setup_inspector import _get_extensions, _get_skills, _get_model, capture_setup_snapshot
```

Test cases:
1. `_get_extensions()` returns parsed list on success (exit 0, valid JSON)
2. `_get_extensions()` returns `[]` on non-zero exit code
3. `_get_extensions()` returns `[]` on `TimeoutError`
4. `_get_extensions()` returns `[]` on JSON parse error
5. `_get_skills()` returns `[]` on non-zero exit (no plain-text fallback)
6. `_get_model()` extracts primary model from JSON
7. `_get_model()` returns `"unknown"` on failure
8. `capture_setup_snapshot()` calls all three concurrently (verify `asyncio.gather` usage)
9. `capture_setup_snapshot()` includes all config fields (`run_id`, `thinking`, `agent`)
10. `_sanitize_for_display()` strips ANSI escape sequences

---

## Technical Considerations

### asyncio vs subprocess.run

The rest of the harness (`agent_driver.py`) uses `asyncio.create_subprocess_exec` for all
subprocess calls. `setup_inspector.py` follows the same pattern to:
- Enable concurrent execution of the three CLI calls via `asyncio.gather`
- Reuse the established kill-on-timeout idiom (`proc.kill()` + `await proc.communicate()`)
  which prevents zombie child processes on timeout

The call site in `run.py` wraps with `asyncio.run(capture_setup_snapshot(config))` since
`run.py` itself is synchronous.

### Subprocess timeout parallelism

Sequential worst case: 30s (3 × 10s timeouts)
Parallel worst case: 10s (single slowest call)

This matters most for single-instance smoke runs where 30s overhead is 50% of run time.

### `openclaw extensions list --json` command name

The snapshot calls `openclaw extensions list --json`. This must be verified against the actual
CLI during implementation. If the subcommand name differs (e.g. `openclaw plugins list`),
update the call accordingly. The JSON field is named `extensions` regardless.

### Tolerant reader / strict writer

All JSON output uses `schema_version: 1`. Readers should:
- Accept unknown keys (forward compatibility)
- Require `schema_version` ≤ current version, warn if higher
- Provide defaults for missing optional keys

### `--dry-run` behaviour

The snapshot **is** captured and printed during `--dry-run` (pre-inference inspection is
read-only; the snapshot does not start inference). However, the snapshot file **is not**
written to disk during `--dry-run` to avoid creating the results directory for a no-op run.

### `--skip-eval` behaviour

Summary shows `"N/A — evaluation skipped (--skip-eval)"` for resolved%. Token usage is still
shown since inference ran.

### Extensions display truncation

Show all names if ≤ 10. If > 10, show first 5 then `"... and N more"`. Full list always in
`setup_snapshot.json`.

---

## Acceptance Criteria

- [ ] `eval/swe-bench/setup_inspector.py` created with:
  - `SetupSnapshot` frozen dataclass with `schema_version`, `extensions`, `skills`, `model`, etc.
  - Three async helpers: `_get_extensions()`, `_get_skills()`, `_get_model()`
  - `capture_setup_snapshot(config)` using `asyncio.gather` for concurrency
  - `asyncio.create_subprocess_exec` + kill-on-timeout (no zombies)
  - `logging` module (not `warnings.warn`)
  - `from __future__ import annotations` as first import
  - `list[dict[str, Any]]` type annotations (not bare `list[dict]`)
- [ ] `eval/swe-bench/run.py` updated:
  - `_sanitize_run_id()` applied to `--run-id` before path construction
  - `capture_setup_snapshot()` called after instance load, before inference
  - `setup_snapshot.json` written to `results/<run_id>/` (with `mkdir(parents=True, exist_ok=True)`)
  - Snapshot NOT written to disk during `--dry-run`
  - `_print_setup_header()` using `[setup]` prefix (plain print style)
  - `_print_run_summary()` using `[summary]` prefix (plain print style, no Unicode box chars)
  - `--skip-eval` shows "N/A — evaluation skipped" not "0%"
  - All-instances-fail guard skips evaluation with clear message
  - `_sanitize_for_display()` applied to all CLI-sourced strings in terminal output
- [ ] `eval/swe-bench/inference.py` updated:
  - `token_usage.json` includes `schema_version: 1`, `generated_at`, `run_id` envelope keys
- [ ] `eval/swe-bench/agent_driver.py` — **no changes required**
- [ ] `eval/swe-bench/tests/test_setup_inspector.py` created with ≥ 10 tests
- [ ] All existing tests still pass
- [ ] Safe fallback: if any snapshot CLI call fails, run continues; field shows `[]` / `"unknown"`
- [ ] Run with `--instances 1 --workers 1` completes and prints full setup + summary

---

## System-Wide Impact

- **Interaction graph**: `run.py` → `capture_setup_snapshot()` (new, pre-inference) →
  `asyncio.gather(get_extensions, get_skills, get_model)` → three concurrent `openclaw` subprocesses.
  No changes to inference path; snapshot is purely observational.
- **Error propagation**: All subprocess errors caught and demoted to `logger.warning`. Run is
  never blocked by snapshot failures.
- **State lifecycle risks**: None — snapshot only reads system state. One new file write
  (`setup_snapshot.json`) is the only side effect.
- **API surface parity**: `token_usage.json` schema change is additive only (`schema_version`,
  `generated_at`, `run_id` fields added).
- **Process cleanup**: `asyncio.create_subprocess_exec` + explicit `proc.kill()` /
  `await proc.communicate()` on timeout prevents orphaned processes.

---

## Dependencies & Risks

| Risk | Mitigation |
|------|-----------|
| `openclaw extensions list --json` command name differs from actual CLI | Verify during Phase 1; update constant if needed |
| Subprocess timeout fires due to cold Node.js startup | 10s is generous; `SUBPROCESS_TIMEOUT` is a named constant for easy tuning |
| `openclaw skills list --json` flag not available in older installs | Return `[]` + `logger.warning`; no plain-text fallback |
| `meta.agentMeta.model` extraction (not used — dropped) | N/A — single source now |
| Path traversal via `--run-id` | `_sanitize_run_id()` applies allowlist `[a-zA-Z0-9\-_]` max 120 chars |
| ANSI injection from CLI-sourced strings | `_sanitize_for_display()` strips `category[0] == "C"` chars |
| **No new Python deps** | `asyncio`, `json`, `logging`, `dataclasses`, `unicodedata` all stdlib |

---

## Implementation Plan

### Phase 1 — `setup_inspector.py` + tests (standalone)

1. Create `eval/swe-bench/setup_inspector.py`
   - `SetupSnapshot` frozen dataclass
   - `_get_extensions()`, `_get_skills()`, `_get_model()` async helpers
   - `capture_setup_snapshot(config)` with `asyncio.gather`
   - `_sanitize_for_display()` helper
2. Create `eval/swe-bench/tests/test_setup_inspector.py`
   - Mock `asyncio.create_subprocess_exec` per test
   - Cover success, timeout, non-zero exit, JSON parse error paths
3. Run `pytest eval/swe-bench/tests/ -v` → all green

### Phase 2 — `run.py` integration

4. Add `_sanitize_run_id()` and apply to `Config.run_id`
5. Add `_print_setup_header()` + `_print_run_summary()` in `run.py`
6. Add `asyncio.run(capture_setup_snapshot(config))` call in `main()`
7. Add snapshot file write (guarded by `not config.dry_run`)
8. Add all-instances-fail guard
9. Replace existing minimal summary with `_print_run_summary()`

### Phase 3 — `token_usage.json` schema update

10. Add `schema_version`, `generated_at`, `run_id` to `token_usage.json` write in `inference.py`
11. Update any test that asserts on `token_usage.json` structure

### Phase 4 — Verification

12. Run `python eval/swe-bench/run.py --instances 1 --workers 1` (or `--dry-run` for fast check)
13. Confirm `results/<run_id>/setup_snapshot.json` exists with valid JSON
14. Confirm terminal output shows `[setup]` header and `[summary]` block
15. Run full test suite: `pytest eval/swe-bench/tests/ -v`

---

## Sources & References

### Internal References

- `eval/swe-bench/run.py` — Main entry point; `_verify_openclaw()` pattern to follow
- `eval/swe-bench/config.py` — `EvalConfig` dataclass, `Optional[str]` fields
- `eval/swe-bench/agent_driver.py` — `asyncio.create_subprocess_exec` + kill-on-timeout pattern; `_sanitize_session_id`
- `eval/swe-bench/inference.py` — `token_usage.json` write; `=== Token Usage Summary ===` print style
- `eval/swe-bench/evaluate.py` — `format_results_summary`; `[check]`/`[eval]` print prefix style
- `src/cli/plugins-cli.ts:364+` — Plugins/extensions CLI
- `src/cli/skills-cli.ts:40+` — Skills CLI
- `src/agents/model-selection.ts` — Model selection logic

### External References

- [Python subprocess docs](https://docs.python.org/3/library/subprocess.html)
- [Python dataclasses docs](https://docs.python.org/3/library/dataclasses.html)
- [asyncio subprocess docs](https://docs.python.org/3/library/asyncio-subprocess.html)
- [unittest.mock docs](https://docs.python.org/3/library/unittest.mock.html)

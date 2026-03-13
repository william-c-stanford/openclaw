---
title: "refactor: Extract token usage tracking into eval/utils shared module"
type: refactor
status: completed
date: 2026-03-13
---

# refactor: Extract token usage tracking into eval/utils shared module

## Overview

Token usage tracking is currently embedded directly inside `eval/swe-bench/inference.py`. Every future benchmark will need the same instrumentation. This refactor extracts it into `eval/utils/token_usage.py` alongside `setup_inspector.py`, so any benchmark can import it without duplicating logic.

A secondary audit identifies `_verify_openclaw()` in `eval/swe-bench/run.py` as another benchmark-agnostic helper that belongs in `eval/utils/setup_inspector.py`.

## Problem Statement / Motivation

`eval/swe-bench/inference.py` contains:
- Four inline accumulator variables (`total_input`, `total_output`, `total_cache_read`, `total_cache_write`, `instances_run`)
- An inline `print("=== Token Usage Summary ===")` block
- A hand-built `token_usage` dict with `schema_version`, `generated_at`, `run_id`, etc.
- A file write of `token_usage.json`

When the next benchmark is added, all of this will need to be copy-pasted or re-implemented. The `setup_inspector.py` precedent shows the right pattern: shared frozen dataclass + thin mutable accumulator, with a `.to_dict()` for JSON serialization.

Additionally, `_verify_openclaw()` in `eval/swe-bench/run.py` is purely about checking the OpenClaw binary and returning its version — it has nothing to do with SWE-bench specifically.

## Proposed Solution

### 1. `eval/utils/token_usage.py` (new)

A frozen `TokenUsage` dataclass that matches the existing `token_usage.json` schema exactly, plus a mutable `TokenUsageAccumulator` that benchmarks use during inference to collect per-instance token counts.

```python
# eval/utils/token_usage.py

@dataclass(frozen=True)
class TokenUsage:
    schema_version: int
    run_id: str
    generated_at: str
    instances_total: int
    instances_run: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    avg_tokens_per_instance: int

    def to_dict(self) -> dict[str, Any]: ...


class TokenUsageAccumulator:
    """Mutable accumulator. Call add() per instance, snapshot() or save() at end."""

    def add(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None: ...

    def snapshot(self, run_id: str, instances_total: int) -> TokenUsage: ...

    def save(self, path: Path, run_id: str, instances_total: int) -> TokenUsage:
        """Build snapshot, write token_usage.json, return the snapshot."""
        ...
```

The `save()` method handles the full write lifecycle (mkdir, json.dumps with indent=2, write_text), so benchmarks just call one method.

### 2. `eval/utils/setup_inspector.py` — add `verify_openclaw()` (modify)

Move `_verify_openclaw()` from `eval/swe-bench/run.py` into `setup_inspector.py` as a public `verify_openclaw() -> str`. It belongs there because:
- It probes the OpenClaw binary (same domain as the three CLI probes in setup_inspector)
- Every benchmark needs it before calling `capture_setup_snapshot(..., openclaw_version=...)`
- It keeps `eval/swe-bench/run.py` clean of CLI plumbing

```python
# In eval/utils/setup_inspector.py (new public function)
def verify_openclaw() -> str:
    """Check openclaw is installed and return its version string.

    Raises RuntimeError if openclaw is not found or version check fails.
    """
    ...
```

### 3. `eval/swe-bench/inference.py` — use TokenUsageAccumulator (modify)

Replace the five inline variables and hand-built dict with:

```python
# eval/swe-bench/inference.py (updated)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from token_usage import TokenUsageAccumulator

# inside InferenceRunner.run():
accumulator = TokenUsageAccumulator()
# ...per result:
accumulator.add(result.input_tokens, result.output_tokens,
                result.cache_read_tokens, result.cache_write_tokens)
# ...at end:
token_usage = accumulator.save(
    token_usage_path, self.config.run_id, total
)
```

The inline `print("=== Token Usage Summary ===")` block in inference.py stays — it's the mid-run progress print, not the final summary (which lives in `run.py`'s `_print_run_summary`).

### 4. `eval/swe-bench/run.py` — update imports (modify)

- Replace `_verify_openclaw()` definition with an import from `setup_inspector`
- Change `_print_run_summary(snapshot, token_usage: dict[str, Any], ...)` to accept `TokenUsage` instead of a raw dict (type-safe field access, no `.get()` with defaults)

### 5. `eval/utils/tests/test_token_usage.py` (new)

Tests mirroring `test_setup_inspector.py` style:
- `test_accumulator_starts_at_zero`
- `test_accumulator_add_single_instance`
- `test_accumulator_add_multiple_instances`
- `test_snapshot_computes_total_and_avg`
- `test_snapshot_avg_is_zero_when_no_instances_ran`
- `test_snapshot_is_frozen`
- `test_snapshot_to_dict_is_serialisable`
- `test_save_writes_json_file` (uses `tmp_path`)
- `test_save_returns_token_usage`

### 6. `eval/README.md` — document new shared module (modify)

Add a `### token_usage — token usage tracking` subsection parallel to the existing `### setup_inspector` section.

## Technical Considerations

- **Schema compatibility**: The `TokenUsage` dataclass fields must match the existing `token_usage.json` schema exactly so existing result files remain valid.
- **`avg_tokens_per_instance` computation**: Computed as `(input + output) // instances_run` — integer division, 0 when `instances_run == 0`. Same as today.
- **`total_tokens` = input + output**: Cache tokens are not included in total (same convention as today).
- **`save()` write failure**: Should be non-fatal in benchmarks — wrap in `try/except OSError` at the call site (same pattern as `setup_snapshot.json` write in run.py).
- **`sys.path.insert` pattern**: Same `parent.parent / "utils"` pattern used in setup_inspector imports.
- **`verify_openclaw()` error behavior**: Keeps `raise RuntimeError(...)` (not graceful degradation) because a missing openclaw binary is a hard prerequisite, not a soft probe.

## System-Wide Impact

- **Interaction graph**: `inference.py` calls `accumulator.add()` per future result → `accumulator.save()` writes `token_usage.json` → `run.py` reads it back for `_print_run_summary`. No callbacks or middleware involved.
- **Error propagation**: `save()` write failure is non-fatal; caller wraps in try/except. `verify_openclaw()` raises `RuntimeError` on failure — same as today.
- **State lifecycle risks**: `token_usage.json` is written once at the end of inference. No partial-write risk beyond what exists today.
- **API surface parity**: Only `eval/swe-bench` currently consumes these utilities. New benchmarks will import the same shared API.
- **Integration test scenarios**: None needed beyond unit tests — there are no cross-layer callbacks here.

## Acceptance Criteria

- [ ] `eval/utils/token_usage.py` exists with `TokenUsage` (frozen dataclass) and `TokenUsageAccumulator`
- [ ] `TokenUsage.to_dict()` round-trips through `json.loads(json.dumps(...))`
- [ ] `TokenUsage` is immutable (mutation raises `AttributeError`/`TypeError`)
- [ ] `TokenUsageAccumulator.save()` creates `token_usage.json` with the same schema as today
- [ ] `verify_openclaw()` added to `eval/utils/setup_inspector.py`, removed from `eval/swe-bench/run.py`
- [ ] `eval/swe-bench/inference.py` imports and uses `TokenUsageAccumulator` from utils
- [ ] `eval/swe-bench/run.py` imports `TokenUsage` and `verify_openclaw` from utils
- [ ] `_print_run_summary` uses `TokenUsage` type instead of `dict[str, Any]`
- [ ] All 9 new tests in `test_token_usage.py` pass
- [ ] All existing tests (21 in `test_setup_inspector.py`) still pass
- [ ] `eval/README.md` documents `token_usage` under Shared utilities
- [ ] A 1-instance dry-run and live run produce identical output to before

## Success Metrics

- Zero regressions: existing `test_setup_inspector.py` (21 tests) pass unchanged
- New `test_token_usage.py` contains ≥ 9 tests, all passing
- `eval/swe-bench/inference.py` contains no inline token accumulation variables
- `eval/swe-bench/run.py` contains no `_verify_openclaw` definition

## Dependencies & Risks

- **No new dependencies**: `token_usage.py` uses only stdlib (`dataclasses`, `json`, `pathlib`, `datetime`)
- **Schema must not change**: The JSON field names and types written to `token_usage.json` must be identical to what inference.py writes today — any consumers (future analysis scripts) should not break
- **`verify_openclaw()` signature change**: It moves from `run.py` (private `_verify_openclaw`) to `setup_inspector.py` (public `verify_openclaw`). The call site in `run.py` simply changes the import.

## Sources & References

- Pattern: `eval/utils/setup_inspector.py` — frozen dataclass + to_dict pattern
- Token tracking today: `eval/swe-bench/inference.py:33-115`
- Run summary consumer: `eval/swe-bench/run.py:122-163`
- Verify openclaw: `eval/swe-bench/run.py:39-58`
- Test pattern: `eval/utils/tests/test_setup_inspector.py`
- Docs to update: `eval/README.md:50-110`

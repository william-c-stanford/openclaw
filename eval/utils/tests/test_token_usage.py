"""Tests for eval/utils/token_usage.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make eval/utils importable from this test file.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "utils"))

from token_usage import TokenUsage, TokenUsageAccumulator  # noqa: E402


# ---------------------------------------------------------------------------
# TokenUsageAccumulator — basic accumulation
# ---------------------------------------------------------------------------


def test_accumulator_starts_at_zero() -> None:
    acc = TokenUsageAccumulator()
    usage = acc.snapshot(run_id="r", instances_total=10)
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 0
    assert usage.instances_run == 0
    assert usage.total_tokens == 0
    assert usage.avg_tokens_per_instance == 0


def test_accumulator_add_single_instance() -> None:
    acc = TokenUsageAccumulator()
    acc.add(input_tokens=100, output_tokens=50, cache_read_tokens=10, cache_write_tokens=5)
    usage = acc.snapshot(run_id="r", instances_total=1)
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.cache_read_tokens == 10
    assert usage.cache_write_tokens == 5
    assert usage.instances_run == 1
    assert usage.total_tokens == 150


def test_accumulator_add_multiple_instances() -> None:
    acc = TokenUsageAccumulator()
    acc.add(100, 50)
    acc.add(200, 80)
    acc.add(300, 120)
    usage = acc.snapshot(run_id="r", instances_total=5)
    assert usage.input_tokens == 600
    assert usage.output_tokens == 250
    assert usage.instances_run == 3
    assert usage.instances_total == 5


def test_accumulator_cache_tokens_default_to_zero() -> None:
    acc = TokenUsageAccumulator()
    acc.add(100, 50)  # no cache args
    usage = acc.snapshot(run_id="r", instances_total=1)
    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 0


# ---------------------------------------------------------------------------
# TokenUsage snapshot — computed fields
# ---------------------------------------------------------------------------


def test_snapshot_computes_total_and_avg() -> None:
    acc = TokenUsageAccumulator()
    acc.add(1000, 200)
    acc.add(800, 100)
    usage = acc.snapshot(run_id="r", instances_total=2)
    assert usage.total_tokens == 2100  # (1000+200) + (800+100)
    assert usage.avg_tokens_per_instance == 1050  # 2100 // 2


def test_snapshot_avg_is_zero_when_no_instances_ran() -> None:
    acc = TokenUsageAccumulator()
    usage = acc.snapshot(run_id="r", instances_total=5)
    assert usage.avg_tokens_per_instance == 0


def test_snapshot_schema_version_is_1() -> None:
    acc = TokenUsageAccumulator()
    usage = acc.snapshot(run_id="r", instances_total=0)
    assert usage.schema_version == 1


def test_snapshot_run_id_and_instances_total() -> None:
    acc = TokenUsageAccumulator()
    acc.add(10, 5)
    usage = acc.snapshot(run_id="my-run-123", instances_total=42)
    assert usage.run_id == "my-run-123"
    assert usage.instances_total == 42


# ---------------------------------------------------------------------------
# TokenUsage — immutability and serialisation
# ---------------------------------------------------------------------------


def test_snapshot_is_frozen() -> None:
    acc = TokenUsageAccumulator()
    usage = acc.snapshot(run_id="r", instances_total=0)
    with pytest.raises((AttributeError, TypeError)):
        usage.input_tokens = 999  # type: ignore[misc]


def test_snapshot_to_dict_is_serialisable() -> None:
    acc = TokenUsageAccumulator()
    acc.add(500, 100, 20, 10)
    usage = acc.snapshot(run_id="r", instances_total=3)
    d = usage.to_dict()
    assert json.loads(json.dumps(d)) == d
    assert d["schema_version"] == 1
    assert "generated_at" in d


# ---------------------------------------------------------------------------
# TokenUsageAccumulator.save — file I/O
# ---------------------------------------------------------------------------


def test_save_writes_json_file(tmp_path: Path) -> None:
    acc = TokenUsageAccumulator()
    acc.add(300, 150, 30, 0)
    path = tmp_path / "token_usage.json"
    acc.save(path=path, run_id="save-test", instances_total=2)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["input_tokens"] == 300
    assert data["output_tokens"] == 150
    assert data["run_id"] == "save-test"
    assert data["schema_version"] == 1


def test_save_returns_token_usage(tmp_path: Path) -> None:
    acc = TokenUsageAccumulator()
    acc.add(100, 40)
    usage = acc.save(
        path=tmp_path / "token_usage.json",
        run_id="ret-test",
        instances_total=1,
    )
    assert isinstance(usage, TokenUsage)
    assert usage.input_tokens == 100
    assert usage.instances_run == 1


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    acc = TokenUsageAccumulator()
    nested_path = tmp_path / "a" / "b" / "c" / "token_usage.json"
    acc.save(path=nested_path, run_id="r", instances_total=0)
    assert nested_path.exists()


def test_save_is_nonfatal_on_bad_path() -> None:
    """save() must not raise on an unwritable path — it logs and returns snapshot."""
    acc = TokenUsageAccumulator()
    acc.add(10, 5)
    # /dev/null/x is an unwritable path on macOS/Linux
    bad_path = Path("/dev/null/nonexistent/token_usage.json")
    usage = acc.save(path=bad_path, run_id="r", instances_total=1)
    assert isinstance(usage, TokenUsage)
    assert usage.input_tokens == 10

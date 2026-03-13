#!/usr/bin/env python3
"""
SWE-bench Mini evaluation wrapper for openclaw.

Usage:
    python eval/swe-bench/run.py
    python eval/swe-bench/run.py --instance-filter "sympy__sympy-*"
    python eval/swe-bench/run.py --workers 2 --thinking medium
    python eval/swe-bench/run.py --skip-eval   # inference only
    python eval/swe-bench/run.py --run-id my_test --dry-run
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Make eval/utils importable — shared utilities for all benchmarks.
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from setup_inspector import SetupSnapshot, capture_setup_snapshot, sanitize_for_display, verify_openclaw  # noqa: E402
from token_usage import TokenUsage  # noqa: E402

from datasets import load_dataset

from config import build_config_from_args
from evaluate import run_evaluation
from inference import InferenceRunner


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")




def _load_instances(
    dataset_name: str,
    split: str,
    instance_filter: str | None,
    instances_limit: int | None,
) -> list[dict[str, Any]]:
    print(f"[data] Loading dataset {dataset_name} (split={split})")
    dataset = load_dataset(dataset_name, split=split)

    instances = [dict(row) for row in dataset]

    if instance_filter:
        instances = [
            row
            for row in instances
            if fnmatch.fnmatch(str(row.get("instance_id", "")), instance_filter)
        ]

    if instances_limit is not None:
        instances = instances[:instances_limit]

    return instances


def _print_setup_header(snapshot: SetupSnapshot) -> None:
    """Print the OpenClaw setup configuration before inference begins."""
    print("\n=== OpenClaw Setup ===")
    print(f"[setup] Run ID       : {snapshot.run_id}")
    print(f"[setup] Model        : {sanitize_for_display(snapshot.model)}")
    print(f"[setup] Thinking     : {snapshot.thinking}")
    print(f"[setup] Agent        : {snapshot.agent or '(none)'}")

    ext_count = len(snapshot.extensions)
    print(f"[setup] Extensions   : {ext_count} installed")
    if 0 < ext_count <= 10:
        for ext in snapshot.extensions:
            name = ext.get("name") or ext.get("id") or "?"
            print(f"[setup]                • {sanitize_for_display(str(name))}")
    elif ext_count > 10:
        for ext in snapshot.extensions[:5]:
            name = ext.get("name") or ext.get("id") or "?"
            print(f"[setup]                • {sanitize_for_display(str(name))}")
        print(f"[setup]                ... and {ext_count - 5} more")

    skill_count = len(snapshot.skills)
    print(f"[setup] Skills       : {skill_count} available")
    if 0 < skill_count <= 5:
        for sk in snapshot.skills:
            name = sk.get("name") or "?"
            print(f"[setup]                • {sanitize_for_display(str(name))}")
    elif skill_count > 5:
        names = [
            sanitize_for_display(str(sk.get("name") or "?"))
            for sk in snapshot.skills[:5]
        ]
        print(f"[setup]                • {', '.join(names)} (+ {skill_count - 5} more)")

    print(f"[setup] openclaw     : {sanitize_for_display(snapshot.openclaw_version)}")
    print()


def _print_run_summary(
    snapshot: SetupSnapshot,
    token_usage: TokenUsage,
    eval_results: dict[str, Any] | None,
    skip_eval: bool,
    output_dir: str,
) -> None:
    """Print a consolidated run summary after inference and evaluation."""
    print("\n=== SWE-bench Mini Run Summary ===")
    print(f"[summary] Run ID        : {snapshot.run_id}")
    print(f"[summary] Results dir   : {Path(output_dir) / snapshot.run_id}")

    print("[summary] --- Setup ---")
    print(f"[summary] Model         : {sanitize_for_display(snapshot.model)}")
    print(f"[summary] Thinking      : {snapshot.thinking}")
    print(f"[summary] Agent         : {snapshot.agent or '(none)'}")
    print(f"[summary] Extensions    : {len(snapshot.extensions)}")
    print(f"[summary] Skills        : {len(snapshot.skills)}")
    print(f"[summary] openclaw      : {sanitize_for_display(snapshot.openclaw_version)}")

    print("[summary] --- Performance ---")
    if skip_eval:
        print("[summary] Resolved      : N/A — evaluation skipped (--skip-eval)")
    elif eval_results is not None:
        total = int(eval_results.get("total", 0))
        resolved = int(eval_results.get("resolved", 0))
        pct = (resolved / total * 100.0) if total else 0.0
        print(f"[summary] Resolved      : {resolved} / {total}  ({pct:.1f}%)")
    else:
        print("[summary] Resolved      : N/A — evaluation data unavailable")

    print("[summary] --- Token Usage ---")
    print(f"[summary] Input         : {token_usage.input_tokens:,}")
    print(f"[summary] Output        : {token_usage.output_tokens:,}")
    print(f"[summary] Cache read    : {token_usage.cache_read_tokens:,}")
    print(f"[summary] Cache write   : {token_usage.cache_write_tokens:,}")
    print(f"[summary] Total         : {token_usage.total_tokens:,}")
    if token_usage.instances_run > 0:
        print(f"[summary] Avg / inst    : {token_usage.avg_tokens_per_instance:,}")
    print()


def main(argv: list[str] | None = None) -> int:
    _configure_logging()

    try:
        config = build_config_from_args(argv)
        oclaw_version = verify_openclaw()

        instances = _load_instances(
            dataset_name=config.dataset_name,
            split=config.dataset_split,
            instance_filter=config.instance_filter,
            instances_limit=config.instances_limit,
        )

        print(f"[data] Selected {len(instances)} instances")
        print(f"[run] run_id={config.run_id}")
        print(f"[run] predictions_path={config.predictions_path}")

        # Capture setup snapshot — runs concurrently with 3 CLI probes.
        snapshot = asyncio.run(
            capture_setup_snapshot(
                run_id=config.run_id,
                thinking=config.thinking_level,
                agent=config.openclaw_agent,
                openclaw_version=oclaw_version,
            )
        )
        _print_setup_header(snapshot)

        if config.dry_run:
            print("[dry-run] Exiting before inference/evaluation.")
            # Snapshot not written to disk during dry-run (no results dir created).
            return 0

        if not instances:
            print("[error] No instances matched the current selection/filter.")
            return 1

        # Write snapshot to disk now that we know the run will proceed.
        snapshot_dir = config.predictions_path.parent
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshot_dir / "setup_snapshot.json"
        try:
            snapshot_path.write_text(
                json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"[setup] Snapshot saved : {snapshot_path}")
        except OSError as exc:
            # Snapshot write failure is non-fatal — log and continue.
            logging.warning("[setup] Could not write setup_snapshot.json: %s", exc)

        predictions_path = InferenceRunner(config).run(instances)
        print(f"\n[inference] Predictions written to {predictions_path}")

        # Load token usage for the final summary.
        # Normally written by InferenceRunner; fall back to a zero-value snapshot
        # if the file is missing or unreadable.
        token_usage: TokenUsage
        token_file = predictions_path.parent / "token_usage.json"
        try:
            raw = json.loads(token_file.read_text(encoding="utf-8"))
            token_usage = TokenUsage(**{k: raw[k] for k in TokenUsage.__dataclass_fields__})
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            from token_usage import TokenUsageAccumulator
            token_usage = TokenUsageAccumulator().snapshot(
                run_id=config.run_id, instances_total=len(instances)
            )

        # Guard: if no instances ran successfully, skip evaluation to avoid a
        # misleading FileNotFoundError from the harness when it finds no results.
        instances_run = token_usage.instances_run
        if instances_run == 0 and not config.skip_eval:
            print(
                "[warn] All instances failed inference — skipping evaluation "
                "(no patches to evaluate)."
            )
            _print_run_summary(
                snapshot, token_usage, None, skip_eval=True, output_dir=str(config.output_dir)
            )
            return 1

        if config.skip_eval:
            print("[eval] Skipped (--skip-eval).")
            _print_run_summary(
                snapshot, token_usage, None, skip_eval=True, output_dir=str(config.output_dir)
            )
            return 0

        eval_results = run_evaluation(config, predictions_path)
        _print_run_summary(
            snapshot,
            token_usage,
            eval_results,
            skip_eval=False,
            output_dir=str(config.output_dir),
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_RUN_ID_SAFE = re.compile(r"[^a-zA-Z0-9\-_]")


@dataclass(slots=True)
class Config:
    """Runtime configuration for SWE-bench mini evaluation."""

    dataset_name: str = "MariusHobbhahn/swe-bench-verified-mini"
    dataset_split: str = "test"
    max_workers: int = 4
    timeout_seconds: int = 600
    thinking_level: str = "low"
    run_id: str = ""
    output_dir: Path = Path("eval/swe-bench/results")
    model_name: str = "openclaw"
    openclaw_agent: Optional[str] = None
    skip_eval: bool = False
    instance_filter: Optional[str] = None
    instances_limit: Optional[int] = None
    dry_run: bool = False
    use_local: bool = True
    predictions_path: Path = field(init=False)

    def __post_init__(self) -> None:
        if not self.run_id:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            self.run_id = f"swe-mini-{timestamp}"
        else:
            # Sanitize user-supplied run_id to prevent path traversal.
            # Mirrors the _sanitize_session_id pattern in agent_driver.py.
            safe = _RUN_ID_SAFE.sub("", self.run_id)[:120]
            self.run_id = safe or "run"
        self.predictions_path = self.output_dir / self.run_id / "predictions.jsonl"


def build_config_from_args(argv: Optional[list[str]] = None) -> Config:
    """Parse CLI args and build a validated :class:`Config`."""

    parser = argparse.ArgumentParser(
        description="Run SWE-bench mini inference + evaluation for openclaw."
    )
    parser.add_argument(
        "--dataset",
        default="MariusHobbhahn/swe-bench-verified-mini",
        help="HuggingFace dataset name to load instances from.",
    )
    parser.add_argument(
        "--split",
        default="test",
        help="Dataset split to evaluate.",
    )
    parser.add_argument(
        "--instances",
        type=int,
        default=None,
        help="Limit number of instances processed.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Maximum parallel workers for inference/evaluation.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-instance openclaw timeout (seconds).",
    )
    parser.add_argument(
        "--thinking",
        default="low",
        choices=["low", "medium", "high"],
        help="openclaw thinking level.",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional run identifier. Auto-generated when omitted.",
    )
    parser.add_argument(
        "--output-dir",
        default="eval/swe-bench/results",
        help="Base output directory for predictions and eval artifacts.",
    )
    parser.add_argument(
        "--model-name",
        default="openclaw",
        help="Value written to `model_name_or_path` in predictions JSONL.",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Optional openclaw agent/plugin identifier passed via --agent.",
    )
    parser.add_argument(
        "--skip-eval",
        action="store_true",
        help="Skip swebench harness evaluation and only run inference.",
    )
    parser.add_argument(
        "--instance-filter",
        default=None,
        help="fnmatch pattern for instance_id selection (for example 'sympy__sympy-*').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load and filter instances, then exit without inference/eval.",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--local",
        dest="use_local",
        action="store_true",
        default=True,
        help="Force local mode for openclaw agent execution (default).",
    )
    mode.add_argument(
        "--gateway",
        dest="use_local",
        action="store_false",
        help="Use gateway mode (omit --local).",
    )

    args = parser.parse_args(argv)

    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.timeout < 1:
        parser.error("--timeout must be >= 1")
    if args.instances is not None and args.instances < 1:
        parser.error("--instances must be >= 1 when provided")

    return Config(
        dataset_name=args.dataset,
        dataset_split=args.split,
        max_workers=args.workers,
        timeout_seconds=args.timeout,
        thinking_level=args.thinking,
        run_id=args.run_id,
        output_dir=Path(args.output_dir),
        model_name=args.model_name,
        openclaw_agent=args.agent,
        skip_eval=args.skip_eval,
        instance_filter=args.instance_filter,
        instances_limit=args.instances,
        dry_run=args.dry_run,
        use_local=args.use_local,
    )

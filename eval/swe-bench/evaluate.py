from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from config import Config


def run_evaluation(config: Config, predictions_path: Path) -> dict[str, Any]:
    """Run the SWE-bench harness and return parsed result artifacts."""

    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        config.dataset_name,
        "--predictions_path",
        str(predictions_path),
        "--max_workers",
        str(config.max_workers),
        "--run_id",
        config.run_id,
    ]

    print("\n[eval] Starting SWE-bench harness evaluation...")
    print("[eval] Command:", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    if process.stdout is not None:
        for line in process.stdout:
            print(line, end="")

    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"Evaluation failed with exit code {return_code}")

    results_dir = Path("evaluation_results") / config.run_id
    results = _load_evaluation_results(results_dir)
    print(format_results_summary(results))
    _print_instance_table(results)
    return results


def format_results_summary(results: dict[str, Any]) -> str:
    """Create a short summary string with resolved totals."""

    total = int(results.get("total", 0))
    resolved = int(results.get("resolved", 0))
    pct = (resolved / total * 100.0) if total else 0.0
    return (
        "\n=== SWE-bench Mini Summary ===\n"
        f"Total instances: {total}\n"
        f"Resolved: {resolved}\n"
        f"Resolved %: {pct:.2f}%\n"
    )


def _load_evaluation_results(results_dir: Path) -> dict[str, Any]:
    if not results_dir.exists():
        raise FileNotFoundError(
            f"Expected evaluation output at {results_dir}, but directory was not found."
        )

    summary: dict[str, Any] = {
        "total": 0,
        "resolved": 0,
        "instances": [],
        "results_dir": str(results_dir),
    }

    json_files = sorted(results_dir.rglob("*.json"))
    for path in json_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue

        _merge_result_data(summary, data)

    if not summary["instances"]:
        jsonl_files = sorted(results_dir.rglob("*.jsonl"))
        for path in jsonl_files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _merge_result_data(summary, row)

    if summary["total"] == 0 and summary["instances"]:
        summary["total"] = len(summary["instances"])
    if summary["resolved"] == 0 and summary["instances"]:
        summary["resolved"] = sum(1 for row in summary["instances"] if row.get("resolved"))

    return summary


def _merge_result_data(summary: dict[str, Any], data: Any) -> None:
    if isinstance(data, dict):
        if "total" in data and isinstance(data["total"], int):
            summary["total"] = max(summary["total"], data["total"])
        if "resolved" in data and isinstance(data["resolved"], int):
            summary["resolved"] = max(summary["resolved"], data["resolved"])

        if "resolved_instances" in data and isinstance(data["resolved_instances"], list):
            summary["resolved"] = max(summary["resolved"], len(data["resolved_instances"]))

        if "instances" in data and isinstance(data["instances"], list):
            for row in data["instances"]:
                if isinstance(row, dict):
                    summary["instances"].append(
                        {
                            "instance_id": row.get("instance_id", ""),
                            "resolved": bool(row.get("resolved", False)),
                            "patch_applied": bool(
                                row.get("patch_applied", row.get("applied", False))
                            ),
                        }
                    )

        if "instance_id" in data:
            summary["instances"].append(
                {
                    "instance_id": data.get("instance_id", ""),
                    "resolved": bool(data.get("resolved", False)),
                    "patch_applied": bool(
                        data.get("patch_applied", data.get("applied", False))
                    ),
                }
            )

    elif isinstance(data, list):
        for item in data:
            _merge_result_data(summary, item)


def _print_instance_table(results: dict[str, Any]) -> None:
    instances = results.get("instances", [])
    if not instances:
        print("[eval] No per-instance table data found in evaluation artifacts.")
        return

    print("instance_id | resolved | patch_applied")
    print("--- | --- | ---")
    for row in instances:
        print(
            f"{row.get('instance_id', '')} | "
            f"{str(bool(row.get('resolved', False))).lower()} | "
            f"{str(bool(row.get('patch_applied', False))).lower()}"
        )

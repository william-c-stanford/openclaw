from __future__ import annotations

import json
import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from agent_driver import AgentDriver, InstanceResult
from config import Config

# Make eval/utils importable — shared utilities for all benchmarks.
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from token_usage import TokenUsage, TokenUsageAccumulator  # noqa: E402

try:
    from tqdm import tqdm
except Exception:  # noqa: BLE001
    tqdm = None


class InferenceRunner:
    """Runs inference across a collection of SWE-bench instances."""

    def __init__(self, config: Config):
        self.config = config
        self.driver = AgentDriver(config)

    def run(self, instances: list[dict[str, Any]]) -> Path:
        """Execute inference, write predictions JSONL, and persist token usage summary."""

        output_dir = self.config.predictions_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        total = len(instances)
        future_map: dict[Future[InstanceResult | None], dict[str, Any]] = {}
        accumulator = TokenUsageAccumulator()

        with (
            ThreadPoolExecutor(max_workers=self.config.max_workers) as executor,
            self.config.predictions_path.open("w", encoding="utf-8") as fp,
        ):
            for instance in instances:
                future = executor.submit(self.driver.run_instance_sync, instance)
                future_map[future] = instance

            iterator = as_completed(future_map)
            if tqdm is not None:
                iterator = tqdm(iterator, total=total, desc="Inference", unit="instance")
            else:
                print(f"Running inference for {total} instances...")

            for future in iterator:
                instance = future_map[future]
                instance_id = str(instance.get("instance_id", ""))
                result: InstanceResult | None
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"[error] {instance_id}: inference failed: {exc}")
                    result = None

                patch = result.patch if result and result.patch else ""

                if result is not None:
                    accumulator.add(
                        result.input_tokens,
                        result.output_tokens,
                        result.cache_read_tokens,
                        result.cache_write_tokens,
                    )

                row = {
                    "instance_id": instance_id,
                    "model_name_or_path": self.config.model_name,
                    "model_patch": patch,
                }
                fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                fp.flush()

                if tqdm is None:
                    print(f"[done] {instance_id} | patch_len={len(patch)}")

        token_usage = accumulator.save(
            path=self.config.predictions_path.parent / "token_usage.json",
            run_id=self.config.run_id,
            instances_total=total,
        )

        print()
        print("=== Token Usage Summary ===")
        print(f"Total input tokens:      {token_usage.input_tokens:,}")
        print(f"Total output tokens:     {token_usage.output_tokens:,}")
        print(f"Cache read tokens:       {token_usage.cache_read_tokens:,}")
        print(f"Cache write tokens:      {token_usage.cache_write_tokens:,}")
        print(f"Total tokens consumed:   {token_usage.total_tokens:,}")
        if token_usage.instances_run > 0:
            print(f"Avg tokens per instance: {token_usage.avg_tokens_per_instance:,}")
        print()

        return self.config.predictions_path

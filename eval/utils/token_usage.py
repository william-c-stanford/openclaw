"""
token_usage — shared token usage tracking for OpenClaw eval benchmarks.

Usage in any benchmark under eval/<name>/:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
    from token_usage import TokenUsage, TokenUsageAccumulator

    accumulator = TokenUsageAccumulator()
    # ... per inference result:
    accumulator.add(result.input_tokens, result.output_tokens,
                    result.cache_read_tokens, result.cache_write_tokens)
    # ... at end of inference:
    token_usage = accumulator.save(
        path=output_dir / "token_usage.json",
        run_id=config.run_id,
        instances_total=len(instances),
    )

The TokenUsage dataclass is frozen and JSON-serialisable. The schema is
versioned (schema_version: 1); readers should tolerate unknown keys.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsage:
    """Immutable snapshot of token consumption for a benchmark run.

    Fields
    ------
    schema_version
        Incremented only on breaking schema changes.
    run_id
        The benchmark run identifier.
    generated_at
        ISO-8601 UTC timestamp when the snapshot was created.
    instances_total
        Total number of instances submitted for inference.
    instances_run
        Number of instances that completed inference successfully.
    input_tokens
        Total input tokens across all instances.
    output_tokens
        Total output (generated) tokens across all instances.
    cache_read_tokens
        Total prompt-cache read tokens (if supported by the model).
    cache_write_tokens
        Total prompt-cache write tokens (if supported by the model).
    total_tokens
        input_tokens + output_tokens (cache tokens excluded, matching API billing).
    avg_tokens_per_instance
        total_tokens // instances_run; 0 when instances_run == 0.
    """

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

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict suitable for ``json.dumps``."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Mutable accumulator
# ---------------------------------------------------------------------------


class TokenUsageAccumulator:
    """Collect per-instance token counts during inference, then snapshot at the end.

    Example
    -------
    ::

        acc = TokenUsageAccumulator()
        for result in results:
            acc.add(result.input_tokens, result.output_tokens,
                    result.cache_read_tokens, result.cache_write_tokens)
        usage = acc.save(path, run_id="my-run", instances_total=50)
    """

    def __init__(self) -> None:
        self._input = 0
        self._output = 0
        self._cache_read = 0
        self._cache_write = 0
        self._instances_run = 0

    def add(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        """Record token counts for one successfully completed instance."""
        self._input += input_tokens
        self._output += output_tokens
        self._cache_read += cache_read_tokens
        self._cache_write += cache_write_tokens
        self._instances_run += 1

    def snapshot(self, run_id: str, instances_total: int) -> TokenUsage:
        """Build and return a frozen :class:`TokenUsage` from accumulated counts."""
        total = self._input + self._output
        avg = total // self._instances_run if self._instances_run > 0 else 0
        return TokenUsage(
            schema_version=1,
            run_id=run_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            instances_total=instances_total,
            instances_run=self._instances_run,
            input_tokens=self._input,
            output_tokens=self._output,
            cache_read_tokens=self._cache_read,
            cache_write_tokens=self._cache_write,
            total_tokens=total,
            avg_tokens_per_instance=avg,
        )

    def save(self, path: Path, run_id: str, instances_total: int) -> TokenUsage:
        """Build snapshot, write ``token_usage.json``, and return the snapshot.

        The write is best-effort. On ``OSError`` a warning is logged and the
        snapshot is still returned — callers should not treat a write failure
        as fatal.
        """
        usage = self.snapshot(run_id=run_id, instances_total=instances_total)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(usage.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("[token_usage] Could not write %s: %s", path, exc)
        return usage

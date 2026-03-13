"""
setup_inspector — shared OpenClaw environment probe for eval benchmarks.

Usage in any benchmark under eval/<name>/:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
    from setup_inspector import SetupSnapshot, capture_setup_snapshot

    snapshot = asyncio.run(capture_setup_snapshot(
        run_id=config.run_id,
        thinking=config.thinking_level,
        agent=config.openclaw_agent,
        openclaw_version=version,  # already known from --version check
    ))

The module probes three CLI commands concurrently (10-second timeout each):
  - openclaw plugins list --json   → installed extensions/plugins
  - openclaw skills list --json    → available agent skills
  - openclaw models list --json    → configured primary model

All probes degrade gracefully: any failure (missing binary, timeout, bad JSON)
returns an empty list or "unknown" string — never raises.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Per-CLI-call timeout. Three calls run concurrently so worst-case wall time is
# SUBPROCESS_TIMEOUT, not 3 × SUBPROCESS_TIMEOUT.
SUBPROCESS_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Public data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetupSnapshot:
    """Immutable snapshot of the OpenClaw environment captured before a benchmark run.

    Fields
    ------
    schema_version
        Incremented only on breaking schema changes (removed/renamed keys).
        Readers should accept unknown keys for forward compatibility.
    run_id
        The benchmark run identifier passed in at capture time.
    openclaw_version
        Output of ``openclaw --version`` (already available from the startup check).
    model
        Primary configured model as ``"provider/model"`` (e.g. ``"anthropic/claude-opus-4-6"``).
        Set to ``"unknown"`` when the CLI probe fails.
    thinking
        openclaw thinking level used for this run (``"low"``, ``"medium"``, ``"high"``).
    agent
        openclaw agent/plugin identifier passed via ``--agent``, or ``None``.
    extensions
        List of plugin dicts from ``openclaw plugins list --json`` (``plugins`` array).
        Empty list when the probe fails.
    skills
        List of skill dicts from ``openclaw skills list --json`` (``skills`` array).
        Empty list when the probe fails.
    python_version
        ``sys.version`` of the Python process running the benchmark.
    platform_info
        ``platform.platform()`` of the host machine.
    captured_at
        ISO-8601 UTC timestamp when the snapshot was captured.
    """

    schema_version: int
    run_id: str
    openclaw_version: str
    model: str
    thinking: str
    agent: str | None
    extensions: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    python_version: str
    platform_info: str
    captured_at: str

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict suitable for ``json.dumps``."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _run_cli(*args: str) -> tuple[int, str]:
    """Run ``openclaw <args>`` and return (returncode, stdout).

    Never raises. On any error (missing binary, timeout, OS error) returns
    ``(-1, "")``.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "openclaw",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=SUBPROCESS_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.warning(
                "[setup] openclaw %s timed out after %ds", " ".join(args), SUBPROCESS_TIMEOUT
            )
            return -1, ""
        return (proc.returncode or 0), stdout_bytes.decode("utf-8", errors="replace")
    except FileNotFoundError:
        logger.debug("[setup] openclaw not found in PATH (probe: %s)", args)
        return -1, ""
    except Exception:  # noqa: BLE001
        logger.debug("[setup] _run_cli failed for %s", args, exc_info=True)
        return -1, ""


async def _get_extensions() -> list[dict[str, Any]]:
    """Probe ``openclaw plugins list --json`` and return the ``plugins`` array.

    Returns an empty list when the probe fails for any reason.

    Note: The openclaw CLI uses the ``plugins`` subcommand; the field is named
    ``extensions`` in :class:`SetupSnapshot` to match the repo convention where
    plugins and extensions are the same concept (see ``extensions/*`` in the
    main codebase).
    """
    code, stdout = await _run_cli("plugins", "list", "--json")
    if code != 0:
        logger.warning("[setup] openclaw plugins list --json exited %d — extensions unavailable", code)
        return []
    try:
        data = json.loads(stdout)
        plugins = data.get("plugins", [])
        return plugins if isinstance(plugins, list) else []
    except json.JSONDecodeError:
        logger.debug("[setup] plugins list JSON parse failed: %r", stdout[:200])
        return []


async def _get_skills() -> list[dict[str, Any]]:
    """Probe ``openclaw skills list --json`` and return the ``skills`` array.

    Returns an empty list when the probe fails. There is no plain-text fallback;
    if ``--json`` is unavailable in an older install, the probe degrades to ``[]``.
    """
    code, stdout = await _run_cli("skills", "list", "--json")
    if code != 0:
        logger.warning("[setup] openclaw skills list --json exited %d — skills unavailable", code)
        return []
    try:
        data = json.loads(stdout)
        skills = data.get("skills", [])
        return skills if isinstance(skills, list) else []
    except json.JSONDecodeError:
        logger.debug("[setup] skills list JSON parse failed: %r", stdout[:200])
        return []


async def _get_model() -> str:
    """Probe ``openclaw models list --json`` and return ``"provider/model"``.

    Extracts the first entry's ``ref.provider`` + ``ref.model``. Returns
    ``"unknown"`` when the probe fails or no models are configured.
    """
    code, stdout = await _run_cli("models", "list", "--json")
    if code != 0:
        logger.warning("[setup] openclaw models list --json exited %d — model unavailable", code)
        return "unknown"
    try:
        data = json.loads(stdout)
        models = data.get("models", [])
        if models and isinstance(models, list):
            first = models[0]
            if not isinstance(first, dict):
                return "unknown"
            # Use the `key` field (format: "provider/model") when present.
            key = str(first.get("key", "")).strip()
            if key:
                return key
            # Fallback: reconstruct from `ref` sub-object if present.
            ref = first.get("ref", {})
            if isinstance(ref, dict):
                provider = str(ref.get("provider", "")).strip()
                model = str(ref.get("model", "")).strip()
                if provider and model:
                    return f"{provider}/{model}"
                if model:
                    return model
        return "unknown"
    except json.JSONDecodeError:
        logger.debug("[setup] models list JSON parse failed: %r", stdout[:200])
        return "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def capture_setup_snapshot(
    run_id: str,
    thinking: str,
    agent: str | None,
    openclaw_version: str = "unknown",
) -> SetupSnapshot:
    """Probe the local OpenClaw installation concurrently and return a snapshot.

    The three CLI probes run in parallel via ``asyncio.gather``, so the worst-
    case latency is ``SUBPROCESS_TIMEOUT`` (10 s), not 30 s.

    Parameters
    ----------
    run_id:
        The benchmark run identifier (already known at call time).
    thinking:
        The openclaw thinking level (``"low"``, ``"medium"``, ``"high"``).
    agent:
        The openclaw agent/plugin identifier, or ``None`` if not set.
    openclaw_version:
        The version string already obtained from ``openclaw --version``.
        Pass it in to avoid an extra subprocess call.
    """
    extensions, skills, model = await asyncio.gather(
        _get_extensions(),
        _get_skills(),
        _get_model(),
    )
    return SetupSnapshot(
        schema_version=1,
        run_id=run_id,
        openclaw_version=openclaw_version,
        model=model,
        thinking=thinking,
        agent=agent,
        extensions=extensions,
        skills=skills,
        python_version=sys.version,
        platform_info=platform.platform(),
        captured_at=datetime.now(timezone.utc).isoformat(),
    )


def sanitize_for_display(value: str, max_len: int = 80) -> str:
    """Strip non-printable (control) characters and truncate for safe terminal output.

    Prevents ANSI escape injection from CLI-sourced strings rendered in the
    terminal summary.
    """
    clean = "".join(c for c in value if unicodedata.category(c)[0] != "C")
    return clean[:max_len]


def verify_openclaw() -> str:
    """Check that ``openclaw`` is installed and return its version string.

    This is a hard prerequisite check — unlike the async probes in
    :func:`capture_setup_snapshot`, this function raises on failure rather than
    degrading gracefully, because a missing binary means inference cannot run.

    Returns
    -------
    str
        The version string from ``openclaw --version`` (e.g. ``"2026.2.24"``).

    Raises
    ------
    RuntimeError
        If ``openclaw`` is not found in PATH or the version check exits non-zero.
    """
    try:
        proc = subprocess.run(
            ["openclaw", "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("openclaw is not installed or not in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"openclaw exists but version check failed: {stderr}") from exc

    version = (proc.stdout or "").strip() or "(unknown)"
    print(f"[check] openclaw detected: {version}")
    return version

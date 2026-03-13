"""Tests for eval/utils/setup_inspector.py."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make eval/utils importable from this test file.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "utils"))

from setup_inspector import (  # noqa: E402
    SetupSnapshot,
    _get_extensions,
    _get_model,
    _get_skills,
    _run_cli,
    capture_setup_snapshot,
    sanitize_for_display,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc(returncode: int, stdout: bytes, *, timeout: bool = False) -> MagicMock:
    """Build a mock asyncio subprocess with controllable communicate()."""
    proc = MagicMock()
    proc.returncode = returncode

    if timeout:
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.kill = MagicMock()
    else:
        proc.communicate = AsyncMock(return_value=(stdout, b""))

    return proc


def _plugins_json(plugins: list[dict]) -> bytes:
    return json.dumps({"workspaceDir": "/tmp", "plugins": plugins, "diagnostics": []}).encode()


def _skills_json(skills: list[dict]) -> bytes:
    return json.dumps({"workspaceDir": "/tmp", "managedSkillsDir": "/tmp/s", "skills": skills}).encode()


def _models_json(models: list[dict]) -> bytes:
    return json.dumps({"count": len(models), "models": models}).encode()


# ---------------------------------------------------------------------------
# _run_cli
# ---------------------------------------------------------------------------


def test_run_cli_returns_stdout_on_success() -> None:
    proc = _make_proc(0, b"hello\n")
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        code, stdout = asyncio.run(_run_cli("--version"))
    assert code == 0
    assert "hello" in stdout


def test_run_cli_returns_minus1_on_file_not_found() -> None:
    with patch(
        "setup_inspector.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError,
    ):
        code, stdout = asyncio.run(_run_cli("--version"))
    assert code == -1
    assert stdout == ""


def test_run_cli_returns_minus1_on_timeout() -> None:
    proc = _make_proc(0, b"", timeout=True)
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        code, stdout = asyncio.run(_run_cli("--version"))
    assert code == -1
    assert stdout == ""
    proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# _get_extensions
# ---------------------------------------------------------------------------


def test_get_extensions_returns_plugins_list() -> None:
    plugins = [{"id": "msteams", "name": "Microsoft Teams"}]
    proc = _make_proc(0, _plugins_json(plugins))
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_extensions())
    assert result == plugins


def test_get_extensions_returns_empty_on_nonzero_exit() -> None:
    proc = _make_proc(1, b"error message")
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_extensions())
    assert result == []


def test_get_extensions_returns_empty_on_json_parse_error() -> None:
    proc = _make_proc(0, b"not json at all")
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_extensions())
    assert result == []


def test_get_extensions_returns_empty_on_timeout() -> None:
    proc = _make_proc(0, b"", timeout=True)
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_extensions())
    assert result == []


# ---------------------------------------------------------------------------
# _get_skills
# ---------------------------------------------------------------------------


def test_get_skills_returns_skills_list() -> None:
    skills = [{"name": "code-review", "eligible": True}]
    proc = _make_proc(0, _skills_json(skills))
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_skills())
    assert result == skills


def test_get_skills_returns_empty_on_nonzero_exit() -> None:
    proc = _make_proc(2, b"")
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_skills())
    assert result == []


def test_get_skills_no_plain_text_fallback() -> None:
    """Plain-text output (when --json is unavailable) must degrade to [] not crash."""
    proc = _make_proc(0, b"skill-a\nskill-b\n")  # plain text, not JSON
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_skills())
    assert result == []


# ---------------------------------------------------------------------------
# _get_model
# ---------------------------------------------------------------------------


def test_get_model_returns_key_field() -> None:
    """The real CLI output uses a 'key' field in 'provider/model' format."""
    models = [{"key": "acme-ai/turbo-9000", "name": "Turbo 9000"}]
    proc = _make_proc(0, _models_json(models))
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_model())
    assert result == "acme-ai/turbo-9000"


def test_get_model_returns_unknown_on_nonzero_exit() -> None:
    proc = _make_proc(1, b"")
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_model())
    assert result == "unknown"


def test_get_model_returns_unknown_on_empty_models_list() -> None:
    proc = _make_proc(0, _models_json([]))
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_model())
    assert result == "unknown"


def test_get_model_returns_unknown_on_json_parse_error() -> None:
    proc = _make_proc(0, b"{bad json}")
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_model())
    assert result == "unknown"


def test_get_model_falls_back_to_ref_when_no_key() -> None:
    """If key is absent, fall back to ref.provider/ref.model."""
    models = [{"ref": {"provider": "acme-ai", "model": "turbo-9000"}}]
    proc = _make_proc(0, _models_json(models))
    with patch("setup_inspector.asyncio.create_subprocess_exec", return_value=proc):
        result = asyncio.run(_get_model())
    assert result == "acme-ai/turbo-9000"


# ---------------------------------------------------------------------------
# capture_setup_snapshot
# ---------------------------------------------------------------------------


def test_capture_setup_snapshot_assembles_fields() -> None:
    plugins = [{"id": "msteams"}]
    skills = [{"name": "code-review"}]
    models = [{"key": "acme-ai/turbo-9000", "name": "Turbo 9000"}]

    def _fake_proc(cmd: str, *args: str, **_: object) -> MagicMock:
        if "plugins" in args:
            return _make_proc(0, _plugins_json(plugins))
        if "skills" in args:
            return _make_proc(0, _skills_json(skills))
        if "models" in args:
            return _make_proc(0, _models_json(models))
        return _make_proc(0, b"")

    with patch("setup_inspector.asyncio.create_subprocess_exec", side_effect=_fake_proc):
        snapshot = asyncio.run(
            capture_setup_snapshot(
                run_id="test-run",
                thinking="high",
                agent="my-agent",
                openclaw_version="2026.3.1",
            )
        )

    assert snapshot.schema_version == 1
    assert snapshot.run_id == "test-run"
    assert snapshot.thinking == "high"
    assert snapshot.agent == "my-agent"
    assert snapshot.openclaw_version == "2026.3.1"
    assert snapshot.extensions == plugins
    assert snapshot.skills == skills
    assert snapshot.model == "acme-ai/turbo-9000"
    assert snapshot.captured_at  # non-empty ISO timestamp


def test_capture_setup_snapshot_is_frozen() -> None:
    """SetupSnapshot must be immutable (frozen=True)."""
    plugins = [{"id": "test"}]
    skills: list = []
    models: list = []

    def _fake_proc(cmd: str, *args: str, **_: object) -> MagicMock:
        if "plugins" in args:
            return _make_proc(0, _plugins_json(plugins))
        if "skills" in args:
            return _make_proc(0, _skills_json(skills))
        return _make_proc(0, _models_json(models))

    with patch("setup_inspector.asyncio.create_subprocess_exec", side_effect=_fake_proc):
        snapshot = asyncio.run(
            capture_setup_snapshot(run_id="r", thinking="low", agent=None)
        )

    with pytest.raises((AttributeError, TypeError)):
        snapshot.run_id = "mutated"  # type: ignore[misc]


def test_capture_setup_snapshot_to_dict_is_serialisable() -> None:
    proc_fn: dict[str, bytes] = {
        "plugins": _plugins_json([]),
        "skills": _skills_json([]),
        "models": _models_json([]),
    }

    def _fake_proc(cmd: str, *args: str, **_: object) -> MagicMock:
        for key, payload in proc_fn.items():
            if key in args:
                return _make_proc(0, payload)
        return _make_proc(0, b"")

    with patch("setup_inspector.asyncio.create_subprocess_exec", side_effect=_fake_proc):
        snapshot = asyncio.run(
            capture_setup_snapshot(run_id="r", thinking="low", agent=None)
        )

    d = snapshot.to_dict()
    # Must be round-trippable through JSON
    assert json.loads(json.dumps(d)) == d
    assert d["schema_version"] == 1


# ---------------------------------------------------------------------------
# sanitize_for_display
# ---------------------------------------------------------------------------


def test_sanitize_for_display_strips_ansi_escape() -> None:
    evil = "\x1b[2J\x1b[Hnormal text"
    result = sanitize_for_display(evil)
    assert "\x1b" not in result
    assert "normal text" in result


def test_sanitize_for_display_truncates_to_max_len() -> None:
    long_str = "a" * 200
    assert len(sanitize_for_display(long_str, max_len=80)) == 80


def test_sanitize_for_display_preserves_normal_text() -> None:
    normal = "acme-ai/turbo-9000"
    assert sanitize_for_display(normal) == normal

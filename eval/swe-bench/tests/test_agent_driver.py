from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_driver import AgentDriver
from config import Config


config = Config()
driver = AgentDriver(config)


def test_sanitize_allows_only_safe_chars() -> None:
    sanitized = AgentDriver._sanitize_session_id("sympy__sympy-1234")
    assert all(ch.isalnum() or ch in "-_" for ch in sanitized)


def test_sanitize_truncates_long_ids() -> None:
    sanitized = AgentDriver._sanitize_session_id("a" * 200)
    assert len(sanitized) <= 120


def test_build_clone_url_adds_github() -> None:
    clone_url = AgentDriver._build_clone_url("sympy/sympy")
    assert clone_url == "https://github.com/sympy/sympy.git"


def test_build_clone_url_passthrough() -> None:
    clone_url = AgentDriver._build_clone_url("https://github.com/foo/bar.git")
    assert clone_url == "https://github.com/foo/bar.git"


def test_extract_text_top_level_payloads() -> None:
    output = '{"payloads": [{"text": "WORKING", "mediaUrl": null}], "meta": {}}'
    assert driver._extract_text_from_openclaw_output(output) == ["WORKING"]


def test_extract_text_nested_payload() -> None:
    output = '{"ok": true, "payload": {"payloads": [{"text": "hello"}]}}'
    assert driver._extract_text_from_openclaw_output(output) == ["hello"]


def test_extract_text_plain_fallback() -> None:
    assert driver._extract_text_from_openclaw_output("Here is the answer") == ["Here is the answer"]


def test_extract_text_empty() -> None:
    assert driver._extract_text_from_openclaw_output("") == []


def test_extract_text_multi_payloads() -> None:
    output = '{"payloads": [{"text": "first"}, {"text": "second"}], "meta": {}}'
    assert driver._extract_text_from_openclaw_output(output) == ["first", "second"]

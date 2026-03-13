from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from prompts import PATCH_EXTRACTION_PROMPT, build_task_prompt, extract_patch_from_text


def test_build_task_prompt_contains_problem() -> None:
    prompt = build_task_prompt("foo bar baz")
    assert "foo bar baz" in prompt


def test_build_task_prompt_has_patch_instructions() -> None:
    prompt = build_task_prompt("example")
    assert "<patch>" in prompt
    assert "Do NOT modify tests" in prompt


def test_extract_patch_from_tags() -> None:
    text = "before <patch>diff --git a/x.py b/x.py</patch> after"
    patch = extract_patch_from_text(text)
    assert patch is not None
    assert "diff --git" in patch


def test_extract_patch_case_insensitive() -> None:
    text = "<PATCH>diff --git a/x.py b/x.py</PATCH>"
    patch = extract_patch_from_text(text)
    assert patch is not None
    assert "diff --git" in patch


def test_extract_patch_fallback_diff_git() -> None:
    text = "I found the bug\ndiff --git a/foo.py b/foo.py\n--- a/foo.py"
    patch = extract_patch_from_text(text)
    assert patch is not None
    assert "diff --git a/foo.py b/foo.py" in patch


def test_extract_patch_empty_returns_none() -> None:
    assert extract_patch_from_text("") is None


def test_extract_patch_no_diff_returns_none() -> None:
    assert extract_patch_from_text("I looked but found nothing") is None


def test_patch_extraction_prompt_nonempty() -> None:
    assert isinstance(PATCH_EXTRACTION_PROMPT, str)
    assert len(PATCH_EXTRACTION_PROMPT) > 0

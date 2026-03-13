from __future__ import annotations

import re
from typing import Optional

TASK_PROMPT_TEMPLATE = """You are solving a SWE-bench task.

This is a real GitHub issue and your goal is to fix it correctly.

Problem statement:
{problem_statement}

Repository context:
- The target repository is already cloned in your current working directory.
- You can inspect files, reason about the bug, and make a minimal code fix.

Hard requirements:
1. Explore the code and understand the root cause before editing.
2. Implement the minimal correct fix for this issue.
3. Do NOT modify tests.
4. Do NOT add new files.
5. Keep changes as small and focused as possible.

Output contract (critical):
- Your final answer MUST contain ONLY a unified git diff wrapped exactly in:
<patch>
...diff here...
</patch>
- If the patch is missing these tags, the submission will be treated as empty.
"""

PATCH_EXTRACTION_PROMPT = """Please provide ONLY the final patch now.

Return EXACTLY a unified git diff for your current workspace changes, wrapped in:
<patch>
...diff --git ...
</patch>

Do not include explanations or any extra text outside the tags.
"""

_PATCH_TAG_RE = re.compile(r"<patch>(.*?)</patch>", re.IGNORECASE | re.DOTALL)
_DIFF_START_RE = re.compile(r"(^|\n)(diff --git\s+a/.*)", re.DOTALL)


def build_task_prompt(problem_statement: str) -> str:
    """Build the primary agent prompt for one SWE-bench instance."""

    return TASK_PROMPT_TEMPLATE.format(problem_statement=problem_statement.strip())


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", stripped)
        stripped = stripped[:-3].rstrip()
    return stripped


def extract_patch_from_text(text: str) -> Optional[str]:
    """Extract a unified diff from agent output.

    Extraction order:
    1) <patch>...</patch> tags
    2) Fallback to any block beginning with "diff --git"
    """

    if not text or not text.strip():
        return None

    tag_match = _PATCH_TAG_RE.search(text)
    if tag_match:
        candidate = _strip_code_fences(tag_match.group(1))
        candidate = candidate.strip()
        if candidate:
            return candidate

    diff_match = _DIFF_START_RE.search(text)
    if diff_match:
        diff_block = diff_match.group(2)
        diff_block = _strip_code_fences(diff_block).strip()
        if diff_block:
            return diff_block

    return None

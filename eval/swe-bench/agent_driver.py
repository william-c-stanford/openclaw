from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Iterable

from git import Repo

from config import Config
from prompts import PATCH_EXTRACTION_PROMPT, build_task_prompt, extract_patch_from_text

logger = logging.getLogger(__name__)


@dataclass
class InstanceResult:
    patch: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class AgentDriver:
    """Runs openclaw against a single SWE-bench instance and extracts a patch."""

    def __init__(self, config: Config):
        self.config = config

    async def run_instance(self, instance: dict[str, Any]) -> InstanceResult | None:
        """Run one instance end-to-end and return patch + token usage, if available."""

        instance_id = str(instance.get("instance_id", "unknown-instance"))
        repo_name = str(instance.get("repo", "")).strip()
        base_commit = str(instance.get("base_commit", "")).strip()
        problem_statement = str(instance.get("problem_statement", "")).strip()

        if not repo_name or not base_commit or not problem_statement:
            logger.error(
                "Instance %s missing required fields (repo/base_commit/problem_statement).",
                instance_id,
            )
            return None

        session_id = f"swe-{self._sanitize_session_id(instance_id)}"
        temp_dir = Path(tempfile.mkdtemp(prefix=f"swebench-{instance_id}-"))
        repo_dir = temp_dir / "repo"

        usage_totals = {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
        }

        try:
            clone_url = self._build_clone_url(repo_name)
            logger.info("[%s] Cloning %s at %s", instance_id, clone_url, base_commit)
            await asyncio.to_thread(self._clone_repo_at_commit, clone_url, repo_dir, base_commit)

            prompt = build_task_prompt(problem_statement)
            combined_text, usage = await self._run_openclaw(repo_dir, session_id, prompt)
            self._accumulate_usage(usage_totals, usage)

            patch = extract_patch_from_text(combined_text)
            if patch:
                return self._build_instance_result(patch, usage_totals)

            logger.info("[%s] No patch tags found, requesting explicit patch extraction", instance_id)
            follow_up_text, follow_up_usage = await self._run_openclaw(
                repo_dir, session_id, PATCH_EXTRACTION_PROMPT
            )
            self._accumulate_usage(usage_totals, follow_up_usage)

            patch = extract_patch_from_text(follow_up_text)
            if patch:
                return self._build_instance_result(patch, usage_totals)

            logger.info("[%s] Falling back to git diff HEAD", instance_id)
            git_diff = await self._git_diff_head(repo_dir)
            fallback_patch = git_diff.strip() or None
            return self._build_instance_result(fallback_patch, usage_totals)
        except asyncio.TimeoutError:
            logger.error("[%s] Timed out after %ss", instance_id, self.config.timeout_seconds)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] Failed to run instance: %s", instance_id, exc)
            return None
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def run_instance_sync(self, instance: dict[str, Any]) -> InstanceResult | None:
        """Sync wrapper around :meth:`run_instance` for thread-pool execution."""

        return asyncio.run(self.run_instance(instance))

    async def _run_openclaw(
        self, repo_dir: Path, session_id: str, prompt: str
    ) -> tuple[str, dict[str, int]]:
        cmd = ["openclaw", "agent", "--json", "--session-id", session_id]
        if self.config.use_local:
            cmd.append("--local")
        if self.config.openclaw_agent:
            cmd.extend(["--agent", self.config.openclaw_agent])
        cmd.extend(["--thinking", self.config.thinking_level, "--message", prompt])

        logger.debug("Running command in %s: %s", repo_dir, " ".join(cmd[:7]) + " ...")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=self.config.timeout_seconds
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if process.returncode != 0:
            raise RuntimeError(
                f"openclaw agent exited with code {process.returncode}: {stderr.strip()}"
            )

        usage = self._extract_usage_from_openclaw_output(stdout)

        texts = self._extract_text_from_openclaw_output(stdout)
        if texts:
            return "\n".join(t for t in texts if t.strip()), usage

        if stdout.strip():
            return stdout, usage

        if stderr.strip():
            return stderr, usage

        return "", usage

    @staticmethod
    def _clone_repo_at_commit(clone_url: str, repo_dir: Path, commit_sha: str) -> None:
        repo = Repo.clone_from(clone_url, str(repo_dir))
        repo.git.checkout(commit_sha)

    @staticmethod
    def _build_clone_url(repo_name: str) -> str:
        if repo_name.startswith("http://") or repo_name.startswith("https://"):
            return repo_name
        return f"https://github.com/{repo_name}.git"

    @staticmethod
    def _sanitize_session_id(instance_id: str) -> str:
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        sanitized = "".join(ch if ch in allowed else "-" for ch in instance_id)
        return sanitized[:120]

    async def _git_diff_head(self, repo_dir: Path) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "HEAD",
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"git diff failed: {stderr_text.strip()}")
        return stdout.decode("utf-8", errors="replace")

    def _extract_text_from_openclaw_output(self, stdout: str) -> list[str]:
        """Parse JSONL/single-JSON openclaw output and collect payload text chunks."""

        texts: list[str] = []
        parsed_any = False

        for line in stdout.splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except JSONDecodeError:
                continue
            parsed_any = True
            texts.extend(self._extract_texts_from_obj(obj))

        if parsed_any:
            return texts

        decoded_objs = list(self._decode_json_objects(stdout))
        for obj in decoded_objs:
            texts.extend(self._extract_texts_from_obj(obj))

        if texts:
            return texts

        if not parsed_any and not decoded_objs:
            stripped = stdout.strip()
            if stripped and "{" not in stripped and "}" not in stripped:
                return [stdout]

        return []

    @staticmethod
    def _extract_usage_from_obj(obj: dict[str, Any]) -> dict[str, int]:
        """Extract usage fields from one openclaw JSON message."""

        usage_info = {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
        }

        meta = obj.get("meta")
        if not isinstance(meta, dict):
            return usage_info

        agent_meta = meta.get("agentMeta")
        if not isinstance(agent_meta, dict):
            return usage_info

        usage = agent_meta.get("usage")
        if isinstance(usage, dict):
            usage_info["input"] = _coerce_int(usage.get("input"))
            usage_info["output"] = _coerce_int(usage.get("output"))

        last_call_usage = agent_meta.get("lastCallUsage")
        if isinstance(last_call_usage, dict):
            usage_info["cache_read"] = _coerce_int(last_call_usage.get("cacheRead"))
            usage_info["cache_write"] = _coerce_int(last_call_usage.get("cacheWrite"))

        return usage_info

    def _extract_usage_from_openclaw_output(self, stdout: str) -> dict[str, int]:
        """Parse openclaw output and accumulate usage across JSON objects."""

        totals = {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
        }

        parsed_any = False
        for line in stdout.splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except JSONDecodeError:
                continue
            parsed_any = True
            self._accumulate_usage(totals, self._extract_usage_from_obj(obj))

        if parsed_any:
            return totals

        for obj in self._decode_json_objects(stdout):
            self._accumulate_usage(totals, self._extract_usage_from_obj(obj))

        return totals

    def _decode_json_objects(self, data: str) -> Iterable[dict[str, Any]]:
        """Decode one or more JSON objects from free-form stdout."""

        stripped = data.strip()
        if not stripped:
            return []

        decoder = json.JSONDecoder()
        idx = 0
        objs: list[dict[str, Any]] = []
        length = len(stripped)

        while idx < length:
            while idx < length and stripped[idx].isspace():
                idx += 1
            if idx >= length:
                break
            try:
                obj, end = decoder.raw_decode(stripped, idx)
            except JSONDecodeError:
                break
            if isinstance(obj, dict):
                objs.append(obj)
            idx = end

        if objs:
            return objs

        try:
            single = json.loads(stripped)
        except JSONDecodeError:
            return []

        if isinstance(single, dict):
            return [single]
        if isinstance(single, list):
            return [item for item in single if isinstance(item, dict)]
        return []

    def _extract_texts_from_obj(self, obj: dict[str, Any]) -> list[str]:
        """Extract `text` entries from known openclaw JSON response shapes."""

        texts: list[str] = []
        payload = obj.get("payload")

        if isinstance(payload, dict):
            payloads = payload.get("payloads")
            if isinstance(payloads, list):
                for item in payloads:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str):
                            texts.append(text)
            text_value = payload.get("text")
            if isinstance(text_value, str):
                texts.append(text_value)

        top_payloads = obj.get("payloads")
        if isinstance(top_payloads, list):
            for item in top_payloads:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        texts.append(text)

        top_text = obj.get("text")
        if isinstance(top_text, str):
            texts.append(top_text)

        return texts

    @staticmethod
    def _build_instance_result(patch: str | None, usage_totals: dict[str, int]) -> InstanceResult:
        return InstanceResult(
            patch=patch,
            input_tokens=usage_totals.get("input", 0),
            output_tokens=usage_totals.get("output", 0),
            cache_read_tokens=usage_totals.get("cache_read", 0),
            cache_write_tokens=usage_totals.get("cache_write", 0),
        )

    @staticmethod
    def _accumulate_usage(target: dict[str, int], increment: dict[str, int]) -> None:
        target["input"] += increment.get("input", 0)
        target["output"] += increment.get("output", 0)
        target["cache_read"] += increment.get("cache_read", 0)
        target["cache_write"] += increment.get("cache_write", 0)


def _coerce_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0

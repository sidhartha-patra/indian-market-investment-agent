"""Wrapper around the `gh models` CLI extension.

Uses the same GitHub auth as the user's `gh` CLI session — no API keys needed.
Falls back gracefully if `gh` or the extension is not installed.
"""
from __future__ import annotations
import json
import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")


def is_available() -> bool:
    """Return True iff the `gh` CLI is on PATH (gh-models extension assumed installed)."""
    return shutil.which("gh") is not None


def run(prompt: str, system: str | None = None,
        model: str = DEFAULT_MODEL, timeout: int = 120) -> str | None:
    """Run a single-shot prompt through `gh models run` and return the text reply.

    Returns None on failure.
    """
    if not is_available():
        logger.warning("`gh` CLI not found on PATH")
        return None

    args = ["gh", "models", "run", model]
    if system:
        args += ["--system-prompt", system]
    try:
        result = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
        )
        if result.returncode != 0:
            logger.error("gh models failed: %s", result.stderr.strip()[:400])
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error("gh models timed out after %ds", timeout)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("gh models error: %s", exc)
        return None


def run_json(prompt: str, system: str | None = None,
             model: str = DEFAULT_MODEL) -> dict | list | None:
    """Run a prompt and parse the response as JSON.

    Strips common markdown fences (```json ... ```) before parsing.
    """
    raw = run(prompt, system=system, model=model)
    if not raw:
        return None
    # Strip code fences if model added them
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        # Try to find first {...} block
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                pass
        logger.error("LLM did not return valid JSON: %s | raw=%s", exc, raw[:300])
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Available:", is_available())
    out = run("Say hello in 5 words.")
    print("Reply:", out)

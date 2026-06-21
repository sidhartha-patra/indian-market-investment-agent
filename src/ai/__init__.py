"""Provider-agnostic Gen-AI layer for grounded fundamental analysis.

Default provider is **GitHub Models** (free, no paid key), but the same code uses
Anthropic Claude (incl. Opus), Azure OpenAI, OpenAI, or Google Gemini when their key
is present — set once, switch with zero code change. Everything degrades gracefully to
a deterministic, rules-based analysis when no LLM is reachable, so builds never break.
"""
from src.ai.llm import chat, chat_json, is_available, provider_info  # noqa: F401

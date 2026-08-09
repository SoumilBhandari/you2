"""The model boundary: one base_url, one interface, one deterministic fake."""

from __future__ import annotations

from . import prompts, rules
from .client import LLM, LLMError, LLMUnavailable, Message, Usage, extract_json, sentences
from .fake import FakeLLM

__all__ = [
    "LLM", "FakeLLM", "LLMError", "LLMUnavailable", "Message", "Usage",
    "extract_json", "prompts", "rules", "sentences",
]

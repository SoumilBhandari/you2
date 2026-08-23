"""The model is one `base_url` string.

Local llama.cpp, a box on the LAN, and a cloud API are the same code path.
This is the single most important architectural decision in the project
because it defers a hardware commitment indefinitely and lets the answer
change as prices and models do.

stdlib only. `requests` is a fine library and it is also 500 KB of RSS on a
2 GB board that is already running an audio stack, for an HTTP POST and an
SSE parse.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from ..config import DEFAULT, BrainConfig

Message = dict[str, str]


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    """Network or provider failure after retries.

    A distinct type because it is *recoverable*: the intent router and the
    memory-answerable path both keep working offline, and the caller should
    fall back rather than fail the turn.
    """


@dataclass(slots=True)
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    failures: int = 0

    def add(self, prompt: int, completion: int, seconds: float) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.seconds += seconds

    def as_dict(self) -> dict[str, float | int]:
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "seconds": round(self.seconds, 2), "failures": self.failures}


def extract_json(text: str) -> Any:
    """Pull a JSON value out of a model response.

    Small models fence their JSON, prefix it with "Here's the JSON:", or
    append a closing remark. Failing the whole consolidation batch over a
    stray backtick is not acceptable, so this is deliberately forgiving.
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```")[1] if "```" in s[3:] else s[3:]
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = s.find(opener)
        end = s.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"no JSON in response: {text[:200]!r}")


@dataclass
class LLM:
    """A streaming OpenAI-compatible chat client."""

    cfg: BrainConfig = field(default_factory=lambda: DEFAULT)
    api_key: str | None = None
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = (os.environ.get("YOU2_API_KEY")
                            or os.environ.get("GROQ_API_KEY")
                            or os.environ.get("OPENAI_API_KEY") or "")

    # ------------------------------------------------------------- transport

    def _post(self, payload: dict[str, Any]):
        url = self.cfg.llm_base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        last: Exception | None = None
        for attempt in range(self.cfg.llm_max_retries):
            try:
                return urllib.request.urlopen(req, timeout=self.cfg.llm_timeout)
            except urllib.error.HTTPError as exc:
                last = exc
                # 429 and 5xx are worth retrying; 4xx otherwise is our bug.
                if exc.code != 429 and exc.code < 500:
                    raise LLMError(f"{exc.code}: {exc.read()[:300]!r}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            # The free tier's real limit is 30 requests/minute, so back off
            # generously rather than hammering.
            time.sleep(min(8.0, 0.75 * (2 ** attempt)))
        self.usage.failures += 1
        raise LLMUnavailable(f"{self.cfg.llm_base_url}: {last}") from last

    # ---------------------------------------------------------------- public

    def complete(self, messages: Sequence[Message], *, model: str | None = None,
                 temperature: float = 0.2, max_tokens: int = 900,
                 json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": model or self.cfg.llm_model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        with self._post(payload) as resp:
            data = json.loads(resp.read())
        usage = data.get("usage") or {}
        self.usage.add(usage.get("prompt_tokens", 0),
                       usage.get("completion_tokens", 0), time.monotonic() - t0)
        return data["choices"][0]["message"]["content"] or ""

    def structured(self, messages: Sequence[Message], *, model: str | None = None,
                   temperature: float = 0.0, max_tokens: int = 1400) -> Any:
        raw = self.complete(messages, model=model, temperature=temperature,
                            max_tokens=max_tokens, json_mode=True)
        return extract_json(raw)

    def stream(self, messages: Sequence[Message], *, model: str | None = None,
               temperature: float = 0.5, max_tokens: int = 400) -> Iterator[str]:
        """Token deltas as they arrive.

        Never wait for generation to finish. A 40-token reply at 8.8 tok/s is
        4.5 seconds; sentence-chunking this stream into TTS as clauses land
        turns that into ~600 ms of perceived latency, and it is the thing
        every 'local assistant that feels broken' skipped.
        """
        payload = {
            "model": model or self.cfg.llm_model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        t0 = time.monotonic()
        completion = 0
        with self._post(payload) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    event = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    completion += 1
                    yield delta
        self.usage.add(0, completion, time.monotonic() - t0)

    def __repr__(self) -> str:
        return f"LLM({self.cfg.llm_model} @ {self.cfg.llm_base_url})"


def sentences(deltas: Iterator[str], *, min_chars: int = 24) -> Iterator[str]:
    """Chunk a token stream into speakable clauses.

    `min_chars` stops "Yes." and "OK." from each becoming their own TTS call,
    which sounds worse than waiting another 80 ms.
    """
    buf = ""
    for delta in deltas:
        buf += delta
        while True:
            cut = -1
            for i, ch in enumerate(buf):
                if ch in ".!?" and i + 1 < len(buf) and buf[i + 1] in " \n":
                    cut = i + 1
                    break
                if ch in "\n" and i >= min_chars:
                    cut = i + 1
                    break
            if cut == -1 or cut < min_chars:
                break
            out, buf = buf[:cut].strip(), buf[cut:]
            if out:
                yield out
    if buf.strip():
        yield buf.strip()

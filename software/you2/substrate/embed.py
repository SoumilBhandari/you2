"""Embeddings, with an escape hatch.

Two implementations behind one protocol:

**HashEmbedder** -- deterministic, offline, zero dependencies beyond numpy.
The hashing trick over stemmed tokens and character 4-grams, so cosine
similarity measures weighted lexical overlap. It is a genuinely weak semantic
model and it is honest about that. What it buys is enormous: the entire test
suite and the two-year eval harness run with no model download, no network,
and byte-identical results on every machine. A memory system you cannot test
deterministically is a memory system you cannot tune.

**OnnxEmbedder** -- MiniLM-L6-v2 through onnxruntime, for the real device.

Everything downstream depends only on the protocol, so swapping is one line.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol, Sequence, runtime_checkable

import numpy as np

_TOKEN = re.compile(r"[a-z0-9']+")

# Function words carry no retrieval signal and would otherwise dominate the
# overlap measure on short utterances.
_STOP = frozenset("""
a an the and or but if then than that this these those there here of to in on
at by for with from into over under again is am are was were be been being do
does did doing have has had having i you he she it we they me him her them my
your his its our their as so not no nor too very can will just don should now
what which who whom when where why how all any both each few more most other
some such only own same s t d ll m o re ve y
""".split())

_SUFFIXES = ("ations", "ation", "ingly", "edly", "ings", "ing", "ies", "ied",
             "ies", "es", "ed", "ly", "s")


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Return an (n, dim) float32 array of L2-normalised row vectors."""
        ...


def stem(word: str) -> str:
    """Crude suffix stripping. Good enough to make 'meeting'/'meetings' and
    'prefer'/'preferred' land near each other; not a linguistic claim."""
    if len(word) <= 4:
        return word
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[: -len(suf)]
    return word


def tokenize(text: str) -> list[str]:
    out = []
    for raw in _TOKEN.findall(text.lower()):
        if raw in _STOP or len(raw) == 1:
            continue
        out.append(stem(raw))
    return out


def _hash_slots(token: str, dim: int, n_slots: int) -> list[tuple[int, float]]:
    """Map a token to n_slots (index, sign) pairs. blake2b rather than hash()
    because Python randomises str hashing per process and this must be stable
    across runs, machines and years."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
    slots = []
    for i in range(n_slots):
        chunk = digest[i * 4: i * 4 + 4]
        val = int.from_bytes(chunk, "little")
        idx = val % dim
        sign = 1.0 if (val >> 31) & 1 else -1.0
        slots.append((idx, sign))
    return slots


class HashEmbedder:
    """Signed hashing trick over stemmed tokens plus character 4-grams."""

    __slots__ = ("dim", "_slots", "_ngram_weight", "_cache")

    def __init__(self, dim: int = 384, slots: int = 3,
                 ngram_weight: float = 0.30) -> None:
        self.dim = dim
        self._slots = slots
        self._ngram_weight = ngram_weight
        self._cache: dict[str, np.ndarray] = {}

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is None:
                cached = self._encode_one(text)
                if len(self._cache) < 8192:
                    self._cache[text] = cached
            out[row] = cached
        return out

    def _encode_one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        tokens = tokenize(text)
        if not tokens:
            # An all-stopword utterance still needs a stable non-zero vector,
            # or every "ok, thanks" collides at the origin.
            tokens = _TOKEN.findall(text.lower()) or ["\x00empty"]

        counts: dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1

        for token, n in counts.items():
            # Sublinear term frequency: saying a word five times is not five
            # times the evidence.
            w = 1.0 + np.log(n)
            for idx, sign in _hash_slots(token, self.dim, self._slots):
                v[idx] += sign * w

        if self._ngram_weight > 0.0:
            joined = " ".join(tokens)
            w = self._ngram_weight
            for i in range(len(joined) - 3):
                gram = joined[i: i + 4]
                if " " in gram:
                    continue
                for idx, sign in _hash_slots("#" + gram, self.dim, 1):
                    v[idx] += sign * w

        norm = float(np.linalg.norm(v))
        if norm > 0.0:
            v /= norm
        return v

    def __repr__(self) -> str:
        return f"HashEmbedder(dim={self.dim})"


class OnnxEmbedder:
    """MiniLM-L6-v2 via onnxruntime. Imported lazily so the package works
    with neither onnxruntime nor tokenizers installed."""

    def __init__(self, model_dir: str, dim: int = 384, max_len: int = 256) -> None:
        import onnxruntime as ort  # noqa: PLC0415
        from tokenizers import Tokenizer  # noqa: PLC0415

        self.dim = dim
        self._max_len = max_len
        opts = ort.SessionOptions()
        # One thread: the hot path shares an A76 with ASR and TTS, and this
        # runs off the critical path anyway.
        opts.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(f"{model_dir}/model.onnx", opts,
                                          providers=["CPUExecutionProvider"])
        self._tok = Tokenizer.from_file(f"{model_dir}/tokenizer.json")
        self._tok.enable_truncation(max_len)
        self._tok.enable_padding()

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        encs = self._tok.encode_batch(list(texts))
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        names = {i.name for i in self._sess.get_inputs()}
        if "token_type_ids" in names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = self._sess.run(None, feeds)[0]
        m = mask[..., None].astype(np.float32)
        pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
        return (pooled / norms).astype(np.float32)

    def __repr__(self) -> str:
        return f"OnnxEmbedder(dim={self.dim})"


def get_embedder(dim: int = 384, model_dir: str | None = None) -> Embedder:
    """Real model if one is on disk and the runtime is installed; the
    deterministic fallback otherwise. Never fails, never blocks on a
    download."""
    if model_dir:
        try:
            return OnnxEmbedder(model_dir, dim=dim)
        except Exception:  # noqa: BLE001 -- deliberate: fall back, don't crash
            pass
    return HashEmbedder(dim=dim)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

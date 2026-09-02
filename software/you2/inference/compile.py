"""Knowledge compilation: a derivation you keep repeating becomes a fact.

ACT-R calls this production compilation, and it is how skill acquisition
works: the same derivation performed enough times collapses into a single
step. Here it is also the answer to the storage question.

    materialise every implication  ->  combinatorial explosion
    materialise none               ->  re-derive the same thing forever
    materialise on the third use   ->  bounded, and it tracks actual use

The third option is the only one that grows with *use* rather than with
elapsed time, which matters on a device that has to hold ten years in a few
hundred megabytes. Most derivations are drawn once and never again; those
never cost a byte.

A compiled fact keeps `derived_from`, so the truth maintenance system can
invalidate it when a premise is superseded. Without that, compilation is just
a way of manufacturing confident stale claims -- which is exactly the failure
the harness caught in stored insights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge
from .engine import Inference


@dataclass(slots=True)
class Compilation:
    compiled: list[int]
    counted: int
    skipped: int

    def as_dict(self) -> dict[str, int]:
        return {"compiled": len(self.compiled), "counted": self.counted,
                "skipped": self.skipped}


def observe(store: Store, inference: Inference, now: float) -> int:
    """Record that this derivation was drawn. Returns the running count."""
    row = store.conn.execute(
        "SELECT count, compiled_id FROM derivations WHERE signature=?",
        (inference.signature,)).fetchone()
    if row is None:
        store.conn.execute(
            "INSERT INTO derivations(signature, kind, claim, premises, count, "
            "first_at, last_at) VALUES(?,?,?,?,1,?,?)",
            (inference.signature, inference.kind, inference.claim,
             ",".join(str(p) for p in inference.premises), now, now))
        return 1
    count = int(row["count"]) + 1
    store.conn.execute(
        "UPDATE derivations SET count=?, last_at=?, claim=? WHERE signature=?",
        (count, now, inference.claim, inference.signature))
    return count


def compiled_id(store: Store, inference: Inference) -> int | None:
    row = store.conn.execute(
        "SELECT compiled_id FROM derivations WHERE signature=?",
        (inference.signature,)).fetchone()
    return row["compiled_id"] if row and row["compiled_id"] else None


def compile_one(store: Store, inference: Inference, cfg: BrainConfig,
                now: float) -> Neuron | None:
    """Materialise a derivation that has earned it."""
    if compiled_id(store, inference) is not None:
        return None
    if inference.confidence < cfg.inference_compile_confidence:
        return None

    key = f"derived/{inference.signature[:16]}"
    fact = store.add(Neuron(
        kind=Kind.FACT, key=key, content=inference.claim,
        created_at=now, last_access=now, valid_at=now,
        # A compiled inference is never more confident than the derivation
        # that produced it, and it is never treated as something the person
        # said. `derived` is checked when the block is rendered.
        confidence=inference.confidence, salience=0.4, storage_strength=1.5,
        meta={"derived": True, "derived_from": list(inference.premises),
              "rule": inference.kind, "derivation": inference.derivation,
              "cites": list(inference.premises)},
    ))
    for premise in inference.premises:
        if store.soma.get(premise) is not None:
            store.connect(fact.id, premise, Edge.SUPPORTS, 0.85, at=now)
    store.conn.execute(
        "UPDATE derivations SET compiled_id=? WHERE signature=?",
        (fact.id, inference.signature))
    store.log("compiled", {"fact": fact.id, "rule": inference.kind,
                           "confidence": round(inference.confidence, 3),
                           "premises": inference.premises})
    return fact


def sweep(store: Store, inferences: Sequence[Inference], cfg: BrainConfig,
          now: float) -> Compilation:
    """Count tonight's derivations and compile the ones over threshold."""
    out = Compilation(compiled=[], counted=0, skipped=0)
    for inference in inferences:
        count = observe(store, inference, now)
        out.counted += 1
        if count < cfg.inference_compile_threshold:
            out.skipped += 1
            continue
        fact = compile_one(store, inference, cfg, now)
        if fact is not None:
            out.compiled.append(fact.id)
    return out


def decompile(store: Store, fact_id: int) -> None:
    """Undo a compilation, so the claim goes back to being re-derived.

    Used when truth maintenance invalidates a compiled fact: the derivation
    itself may still be valid once premises settle, and forgetting that it was
    ever compiled would let it be compiled again from stale premises.
    """
    store.conn.execute(
        "UPDATE derivations SET compiled_id=NULL, count=0 WHERE compiled_id=?",
        (fact_id,))


def stats(store: Store) -> dict[str, float | int]:
    row = store.conn.execute(
        "SELECT COUNT(*) n, SUM(count) total, "
        "SUM(CASE WHEN compiled_id IS NOT NULL THEN 1 ELSE 0 END) done "
        "FROM derivations").fetchone()
    n = row["n"] or 0
    return {
        "derivations_seen": n,
        "draws": row["total"] or 0,
        "compiled": row["done"] or 0,
        "compile_rate": round((row["done"] or 0) / n, 3) if n else 0.0,
    }

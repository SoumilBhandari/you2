"""Synapses: typed, weighted, plastic.

Two things here do real work and are easy to get wrong.

**Soft-bounded Hebbian learning.** dw = eta * a_i * a_j * (1 - w). Without the
(1 - w) term every edge saturates to 1.0 within a few months and the graph
stops carrying information -- everything is connected to everything, which is
the same as nothing being connected.

**Asymmetric traversal.** Activation has to flow both ways along a directed
edge, but not equally. An entity should reach the episodes that mention it,
or entity cues can never retrieve anything. A superseded fact should be very
hard to reach from its replacement, or supersession does nothing for
retrieval. Hence a forward and a back multiplier per edge type.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Edge(StrEnum):
    ASSOC = "assoc"              # co-activated. Hebbian. The bulk of the graph.
    ABOUT = "about"              # episode -> entity mentioned in it
    PART_OF = "part_of"          # entity -> containing entity
    CAUSES = "causes"            # asserted causation
    SUPERSEDES = "supersedes"    # newer fact -> older fact
    CONTRADICTS = "contradicts"  # two claims that cannot both hold
    NEXT = "next"                # temporal succession within a session
    EVOKES = "evokes"            # asymmetric: retrieving A tends to bring B
    ANALOGOUS = "analogous"      # structural correspondence, not similarity
    SUPPORTS = "supports"        # evidence -> insight


@dataclass(frozen=True, slots=True)
class Conduction:
    forward: float
    back: float
    symmetric: bool = False


#: How readily each edge type conducts activation, in each direction.
#: These are the most consequential constants in retrieval after gamma.
CONDUCTION: dict[Edge, Conduction] = {
    # Plain association is symmetric by definition.
    Edge.ASSOC:       Conduction(1.00, 1.00, symmetric=True),
    # Entity cues MUST be able to reach episodes, or naming a person
    # retrieves nothing. Slightly damped backwards because entities are
    # high-fan and would otherwise flood.
    Edge.ABOUT:       Conduction(1.00, 0.90),
    Edge.PART_OF:     Conduction(0.80, 0.60),
    Edge.CAUSES:      Conduction(0.90, 0.60),
    # A live fact should almost never drag its dead predecessor into the
    # workspace. This single number is most of what supersession buys.
    Edge.SUPERSEDES:  Conduction(0.20, 0.20),
    # A contradiction is exactly the thing you want surfaced.
    Edge.CONTRADICTS: Conduction(0.85, 0.85, symmetric=True),
    Edge.NEXT:        Conduction(0.50, 0.35),
    # Asymmetric on purpose -- that is what it is for.
    Edge.EVOKES:      Conduction(1.00, 0.30),
    Edge.ANALOGOUS:   Conduction(0.70, 0.70, symmetric=True),
    # An insight must be able to reach its evidence (so it can cite);
    # evidence reaching the insight is weaker, so one supporting memory
    # doesn't keep dragging a speculative insight into every conversation.
    Edge.SUPPORTS:    Conduction(0.60, 0.90),
}

#: Edge types exempt from nightly downscaling. Structural and asserted
#: relations are not statistical associations and must not erode.
STRUCTURAL: frozenset[Edge] = frozenset(
    {Edge.ABOUT, Edge.PART_OF, Edge.SUPERSEDES, Edge.CONTRADICTS,
     Edge.CAUSES, Edge.SUPPORTS}
)

#: Compact integer codes, for the in-RAM CSR arrays.
EDGE_CODES: dict[Edge, int] = {e: i for i, e in enumerate(Edge)}
CODE_EDGES: dict[int, Edge] = {i: e for e, i in EDGE_CODES.items()}


def hebbian(weight: float, a_i: float, a_j: float, rate: float) -> float:
    """Soft-bounded Hebbian update.

    The (1 - w) term is what keeps the graph informative. Returns the new
    weight, clamped to [0, 1].
    """
    delta = rate * a_i * a_j * (1.0 - weight)
    return min(1.0, max(0.0, weight + delta))

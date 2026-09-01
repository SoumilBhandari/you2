"""Segmentation: an episode is a bounded exchange, not a turn.

Boundaries are drawn at three things, and the third is the interesting one.

  **Silence.** A long enough gap ends the exchange.
  **Topic shift.** The turn moves away from the running centroid of the
  episode so far.
  **Prediction error.** A surprising statement ends the previous episode and
  starts a new one.

The third comes straight from the event-boundary literature: high-prediction-
error events act as boundaries that interrupt the sequential integration of
what came before. Which is why surprising things are remembered as their own
moment rather than blurred into what preceded them -- and here it means a
surprising remark gets its own episode, its own embedding, and its own shot
at retrieval, instead of being averaged into a conversation about something
else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..perception.listening import Turn
from ..perception.salience import Salience

#: Silence long enough that the next thing said is a new exchange.
GAP_SECONDS = 90.0

#: Cosine distance from the running centroid that counts as a topic shift.
TOPIC_SHIFT = 0.62

#: Surprise above which a turn is treated as an event boundary.
BOUNDARY_SURPRISE = 0.72

MAX_TURNS = 12
MAX_SPAN = 900.0


@dataclass(slots=True)
class Episode:
    turns: list[Turn] = field(default_factory=list)
    saliences: list[Salience] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float = 0.0
    session_id: str | None = None
    boundary_reason: str = ""

    @property
    def text(self) -> str:
        if len(self.turns) == 1:
            return self.turns[0].text.strip()
        return " / ".join(t.text.strip() for t in self.turns)

    @property
    def salience(self) -> float:
        """Max, not mean. One important thing said inside a rambling exchange
        makes the whole exchange worth replaying; averaging it away is how
        the good bit gets lost."""
        return max((s.total for s in self.saliences), default=0.0)

    @property
    def surprise(self) -> float:
        return max((s.surprise for s in self.saliences), default=0.0)

    @property
    def arousal(self) -> float:
        return max((s.arousal for s in self.saliences), default=0.0)

    @property
    def valence(self) -> float:
        vals = [s.valence for s in self.saliences if abs(s.valence) > 1e-6]
        return sum(vals) / len(vals) if vals else 0.0

    def __len__(self) -> int:
        return len(self.turns)

    def __repr__(self) -> str:
        return (f"Episode({len(self.turns)} turns, sal={self.salience:.2f}, "
                f"{self.boundary_reason})")


class Segmenter:
    """Streaming. Feed turns; get a completed episode back when one closes."""

    __slots__ = ("_current", "_centroid", "_embed")

    def __init__(self, embed) -> None:
        self._embed = embed
        self._current: Episode | None = None
        self._centroid: np.ndarray | None = None

    @property
    def open(self) -> Episode | None:
        return self._current

    def feed(self, turn: Turn, salience: Salience) -> Episode | None:
        """Add a turn. Returns the *previous* episode if this turn closed it."""
        closed: Episode | None = None
        vec = self._embed(turn.text)

        if self._current is not None:
            reason = self._boundary(turn, salience, vec)
            if reason:
                self._current.boundary_reason = reason
                self._current.ended_at = self._current.turns[-1].at
                closed = self._current
                self._current = None
                self._centroid = None

        if self._current is None:
            self._current = Episode(started_at=turn.at, session_id=turn.session_id)
            self._centroid = vec.copy()
        else:
            n = len(self._current.turns)
            self._centroid = (self._centroid * n + vec) / (n + 1)

        self._current.turns.append(turn)
        self._current.saliences.append(salience)
        self._current.ended_at = turn.at
        return closed

    def _boundary(self, turn: Turn, salience: Salience,
                  vec: np.ndarray) -> str:
        cur = self._current
        assert cur is not None
        if turn.at - cur.ended_at > GAP_SECONDS:
            return "gap"
        if len(cur.turns) >= MAX_TURNS:
            return "length"
        if turn.at - cur.started_at > MAX_SPAN:
            return "span"
        if salience.surprise >= BOUNDARY_SURPRISE and len(cur.turns) >= 2:
            return "surprise"
        if self._centroid is not None:
            c = self._centroid
            norm = float(np.linalg.norm(c))
            if norm > 0.0:
                sim = float(np.dot(c / norm, vec))
                if 1.0 - sim > TOPIC_SHIFT and len(cur.turns) >= 2:
                    return "topic"
        return ""

    def flush(self, reason: str = "session_end") -> Episode | None:
        """Close whatever is open. Called when the dwell window expires."""
        cur = self._current
        if cur is None:
            return None
        cur.boundary_reason = reason
        self._current = None
        self._centroid = None
        return cur

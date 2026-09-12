"""Listening: the attention state machine.

"Wake-word activated, never always-recording" is a standing decision and it
stays -- other people are in the room and have not agreed to anything. But one
wake word per utterance is not how attention works, and a device built that
way feels deaf.

    DORMANT --wake word--> ATTENDING --turn end--> RESPONDING
       ^                      ^  |                     |
       |                      |  +---dwell expiry------+
       +------dwell expiry----+------follow-up---------+

Three things this buys:

  **Dwell.** After a reply ends, stay attending for ~8 seconds. A follow-up in
  that window needs no wake word. That is sustained attention, it is how
  conversation actually works, and it is the difference between a device you
  talk to and one you issue commands at.

  **Orienting.** The wake word snapshots ambient context -- time of day, gap
  since the last session -- into the cue set before a word is transcribed.

  **Binding.** Pronouns resolve against the workspace, not the transcript,
  which is the payoff for keeping the workspace alive across turns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..clock import Clock, part_of_day, weekday
from ..config import BrainConfig
from .prosody import Prosody


class State(StrEnum):
    DORMANT = "dormant"
    ATTENDING = "attending"
    RESPONDING = "responding"


@dataclass(slots=True)
class Turn:
    text: str
    at: float
    speaker: str = "user"
    source: str = "speech"
    prosody: Prosody | None = None
    onset_latency: float = 0.0
    session_id: str | None = None
    woken: bool = False
    """True if this turn required the wake word. A follow-up inside the dwell
    window is engaged conversation, and that is itself a salience signal."""


@dataclass(slots=True)
class Orienting:
    """What the wake word captures before transcription starts."""
    at: float = 0.0
    weekday: str = ""
    part_of_day: str = ""
    gap_seconds: float = 0.0
    """Time since the last session ended. A wake word at 3 a.m. after eleven
    hours of silence is a different context from the fourth in a minute."""


@dataclass(slots=True)
class Attention:
    cfg: BrainConfig
    clock: Clock
    state: State = State.DORMANT
    session_id: str | None = None
    dwell_until: float = 0.0
    last_activity: float = 0.0
    last_session_end: float = 0.0
    turns: list[Turn] = field(default_factory=list)
    orienting: Orienting | None = None

    # ------------------------------------------------------------ transitions

    def wake(self, now: float | None = None) -> Orienting:
        """Wake word detected. The orienting response."""
        now = now if now is not None else self.clock.now()
        gap = now - self.last_session_end if self.last_session_end else 0.0
        self.orienting = Orienting(at=now, weekday=weekday(now),
                                   part_of_day=part_of_day(now),
                                   gap_seconds=gap)
        if self.state is State.DORMANT:
            self.session_id = f"s{int(now)}"
            self.turns = []
        self.state = State.ATTENDING
        self.last_activity = now
        self.dwell_until = now + self.cfg.dwell_seconds
        return self.orienting

    def accepts(self, now: float | None = None) -> bool:
        """Is the device listening right now, without a new wake word?"""
        now = now if now is not None else self.clock.now()
        self._expire(now)
        return self.state in (State.ATTENDING, State.RESPONDING)

    def hear(self, text: str, now: float | None = None, *,
             prosody: Prosody | None = None, speaker: str = "user",
             source: str = "speech") -> Turn | None:
        """Accept a transcribed turn, or return None if not attending.

        Returning None rather than raising is deliberate: this is the normal
        case dozens of times a day, when someone in the room says something
        that was not addressed to the device.
        """
        now = now if now is not None else self.clock.now()
        self._expire(now)
        if self.state is State.DORMANT:
            return None
        woken = self.orienting is not None and (now - self.orienting.at) < 2.0
        turn = Turn(text=text, at=now, speaker=speaker, source=source,
                    prosody=prosody,
                    onset_latency=max(0.0, now - self.last_activity),
                    session_id=self.session_id, woken=woken)
        self.turns.append(turn)
        self.state = State.ATTENDING
        self.last_activity = now
        self.dwell_until = now + self.cfg.dwell_seconds
        return turn

    def reply_start(self, now: float | None = None) -> None:
        now = now if now is not None else self.clock.now()
        self.state = State.RESPONDING
        self.last_activity = now

    def reply_end(self, now: float | None = None) -> None:
        """Playback finished. The dwell window opens here, not at turn end:
        the user cannot answer while the device is still talking."""
        now = now if now is not None else self.clock.now()
        self.state = State.ATTENDING
        self.last_activity = now
        self.dwell_until = now + self.cfg.dwell_seconds

    def _expire(self, now: float) -> None:
        if self.state is State.ATTENDING and now >= self.dwell_until:
            self.close(now)

    def close(self, now: float | None = None) -> str | None:
        now = now if now is not None else self.clock.now()
        closed = self.session_id
        if self.state is not State.DORMANT:
            self.last_session_end = now
        self.state = State.DORMANT
        self.session_id = None
        self.orienting = None
        return closed

    # ------------------------------------------------------------------ misc

    def ambient(self, now: float | None = None) -> dict[str, object]:
        now = now if now is not None else self.clock.now()
        o = self.orienting
        return {
            "weekday": weekday(now),
            "part_of_day": part_of_day(now),
            "gap_seconds": round(o.gap_seconds, 1) if o else 0.0,
            "turn_index": len(self.turns),
            "session": self.session_id,
        }

    def engagement(self) -> float:
        """How engaged this session is: turn count and how quickly the user
        came back. Feeds salience -- a fast follow-up means the topic mattered
        enough not to wait."""
        if not self.turns:
            return 0.0
        depth = min(1.0, len(self.turns) / 6.0)
        latencies = [t.onset_latency for t in self.turns[1:] if t.onset_latency > 0]
        if not latencies:
            return depth
        mean = sum(latencies) / len(latencies)
        speed = max(0.0, 1.0 - mean / 12.0)
        return max(0.0, min(1.0, 0.6 * depth + 0.4 * speed))

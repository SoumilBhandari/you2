"""The event bus: what the device is doing, as it does it.

Everything the brain does that a person could watch — waking, listening, a
cascade firing, the workspace forming, a night running — is published here.
The display subscribes over SSE, the light bar subscribes in-process, and both
get the same stream.

Two rules make this safe to put on the hot path:

**Publishing never blocks and never raises.** A slow display must not cost a
turn. Subscriber queues are bounded and drop their oldest frame when full,
because a stale animation frame is worthless and a stalled pipeline is not.

**Nothing here is load-bearing.** Unsubscribe every consumer and the brain
behaves identically. The visualisation reads state; it never creates it.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterator


class State(StrEnum):
    """What the device is doing, at a glance.

    These are the states the light bar shows and the screen animates. A voice
    device has to signal its state — light is the only channel that does it
    without interrupting you.
    """
    BOOT = "boot"
    IDLE = "idle"
    LISTENING = "listening"
    REMEMBERING = "remembering"   # the cascade — the one worth watching
    THINKING = "thinking"
    SPEAKING = "speaking"
    DREAMING = "dreaming"         # the night
    ERROR = "error"


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def sse(self) -> bytes:
        payload = json.dumps({"seq": self.seq, **self.data},
                             separators=(",", ":"), default=str)
        return f"event: {self.type}\ndata: {payload}\n\n".encode()


class Bus:
    __slots__ = ("_subs", "_lock", "_seq", "_last_state", "_history")

    #: Per-subscriber buffer. Small: an animation frame that arrives a second
    #: late is worse than one that never arrives.
    DEPTH = 64

    def __init__(self) -> None:
        self._subs: list[queue.Queue[Event]] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._last_state = State.BOOT
        #: Replayed to a client that connects mid-session, so the display does
        #: not come up blank after a browser refresh.
        self._history: list[Event] = []

    @property
    def state(self) -> State:
        return self._last_state

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, type: str, **data: Any) -> Event:
        with self._lock:
            self._seq += 1
            event = Event(type, data, self._seq)
            if type == "state":
                self._last_state = State(data.get("state", State.IDLE))
                self._history = [e for e in self._history if e.type != "state"]
            if type in ("state", "cascade", "workspace", "night"):
                self._history = ([e for e in self._history if e.type != type]
                                 + [event])[-8:]
            for q in self._subs:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    # Drop the oldest, keep the newest. A stale frame is
                    # worthless; a stalled pipeline is not acceptable.
                    try:
                        q.get_nowait()
                        q.put_nowait(event)
                    except queue.Empty:
                        pass
        return event

    def state_changed(self, state: State, **data: Any) -> None:
        if state is not self._last_state or data:
            self.publish("state", state=str(state), **data)

    def subscribe(self) -> queue.Queue[Event]:
        q: queue.Queue[Event] = queue.Queue(maxsize=self.DEPTH)
        with self._lock:
            for event in self._history:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    break
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[Event]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def stream(self, timeout: float = 15.0) -> Iterator[Event]:
        """Blocking iterator for one subscriber. Yields a keepalive on idle so
        a proxy does not close the connection."""
        q = self.subscribe()
        try:
            while True:
                try:
                    yield q.get(timeout=timeout)
                except queue.Empty:
                    yield Event("ping", {})
        finally:
            self.unsubscribe(q)

    def __repr__(self) -> str:
        return f"Bus({self._last_state}, {self.subscribers} subscribers)"


#: The process-wide bus. A device has one brain and one face.
BUS = Bus()

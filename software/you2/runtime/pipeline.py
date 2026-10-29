"""The hot path, assembled.

    hear -> recall -> route -> answer -> [reply] -> broadcast -> encode

Everything to the right of `[reply]` happens after the person has heard the
answer. The hot path acquires no new blocking calls, ever.

Every stage boundary is timestamped. When you are over budget the log tells
you which stage to attack, instead of sending you board-shopping.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..activation import build_cues, compete, spread
from ..activation.workspace import Workspace, availability_fn, broadcast
from ..clock import Clock, RealClock
from ..config import DEFAULT, BrainConfig
from ..consolidation import night
from ..encoding import Segmenter, encode
from ..inference import attach as attach_inferences
from ..llm import prompts
from ..llm.client import LLMUnavailable
from ..perception import Attention, Prosody, Salience, evaluate
from ..perception.prosody import Baseline
from ..recall import reconsolidate
from ..substrate import Store
from ..substrate.embed import Embedder
from . import viz
from .events import BUS, Bus, State
from .router import OFFLINE_FALLBACK, Router, Routed

#: Turns of raw dialogue kept in the prompt. Small on purpose: the memory
#: block is the context, and a long verbatim history both costs tokens and
#: makes the cacheable prefix shorter.
HISTORY_TURNS = 4

#: Handled entirely by the device, and about the device. Nothing here says
#: anything about the person, so none of it is encoded.
DEVICE_INTENTS = frozenset({"time", "timer_set", "timer_cancel", "timer_list"})


@dataclass(slots=True)
class Timing:
    stages: dict[str, float] = field(default_factory=dict)
    _t0: float = 0.0
    _last: float = 0.0

    def start(self) -> None:
        self._t0 = self._last = time.perf_counter()

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.stages[name] = round((now - self._last) * 1000, 2)
        self._last = now

    @property
    def total_ms(self) -> float:
        return round((self._last - self._t0) * 1000, 2)

    def as_dict(self) -> dict[str, float]:
        return {**self.stages, "total": self.total_ms}


@dataclass(slots=True)
class Reply:
    text: str = ""
    route: str = "model"           # router | memory | model | offline | ignored
    workspace: Workspace | None = None
    salience: Salience | None = None
    repair: reconsolidate.Repair | None = None
    encoded: Any = None
    timing: Timing = field(default_factory=Timing)
    cites: list[int] = field(default_factory=list)
    intent: str = ""

    def __repr__(self) -> str:
        return f"Reply({self.route}, {self.timing.total_ms:.0f} ms, {self.text[:48]!r})"


class Brain:
    """One object, the whole system."""

    def __init__(self, cfg: BrainConfig | None = None, *, clock: Clock | None = None,
                 llm: Any | None = None, embedder: Embedder | None = None,
                 path: str | None = None, bus: Bus | None = None) -> None:
        self.cfg = cfg or DEFAULT
        self.clock = clock or RealClock()
        self.store = Store(self.cfg, self.clock, embedder, path)
        self.llm = llm
        self.attention = Attention(self.cfg, self.clock)
        self.segmenter = Segmenter(self.store.embed)
        self.router = Router(self.clock)
        self.baseline = Baseline(self.store, "user")
        self.profile: str = ""
        #: Who they are. Rebuilt nightly, stable all day, and placed ahead
        #: of everything else so the cacheable prefix is as long as possible.
        self.persona: str = ""
        self._context: list[tuple[int, float]] = []
        self._history: list[dict[str, str]] = []
        self._last_workspace: Workspace | None = None
        self._previous_episode: int | None = None
        #: Everything a person could watch is published here. Nothing in the
        #: brain reads it back, so unsubscribing every consumer changes
        #: nothing -- the visualisation observes state, it never creates it.
        self.bus = bus or BUS
        #: One device, one brain, one connection. `Store` opens SQLite with
        #: check_same_thread=False and `serve` runs the night on its own
        #: thread by default, so without this a turn and a consolidation
        #: interleave on the same connection and the same in-RAM indexes --
        #: and a turn landing inside `persist_graph`'s transaction, which
        #: empties and rewrites the whole edge table, is rolled back with it.
        #: Serialising costs nothing: there is only ever one person talking.
        self._lock = threading.RLock()
        self.bus.state_changed(State.IDLE)

    # -------------------------------------------------------------- session

    def wake(self, now: float | None = None):
        orienting = self.attention.wake(now)
        if self.attention.session_id:
            self.store.open_session(self.attention.session_id)
        self.bus.state_changed(State.LISTENING, session=self.attention.session_id)
        return orienting

    def close(self, now: float | None = None) -> None:
        """End the session: flush the open episode and write it."""
        with self._lock:
            self._close(now)

    def _close(self, now: float | None = None) -> None:
        now = now if now is not None else self.clock.now()
        episode = self.segmenter.flush()
        if episode is not None:
            out = encode(self.store, episode, self.cfg,
                         previous=self._previous_episode)
            if out.episode is not None:
                self._previous_episode = out.episode.id
        session = self.attention.close(now)
        if session:
            self.store.close_session(session)
        self._context = []
        self._history = []
        self.store.flush()

    # ------------------------------------------------------------- the turn

    def say(self, text: str, *, now: float | None = None,
            prosody: Prosody | None = None, source: str = "speech",
            wake: bool = True) -> Reply:
        """One turn, start to finish. Serialised against the night."""
        with self._lock:
            return self._say(text, now=now, prosody=prosody, source=source,
                             wake=wake)

    def _say(self, text: str, *, now: float | None = None,
             prosody: Prosody | None = None, source: str = "speech",
             wake: bool = True) -> Reply:
        now = now if now is not None else self.clock.now()
        timing = Timing()
        timing.start()

        if wake and not self.attention.accepts(now):
            self.wake(now)
        turn = self.attention.hear(text, now, prosody=prosody, source=source)
        if turn is None:
            return Reply(route="ignored", timing=timing)
        timing.mark("attend")
        self.bus.publish("transcript", text=text)

        # ---- retrieve ------------------------------------------------------
        cues = build_cues(self.store, text, self.cfg, context=self._context, now=now)
        timing.mark("cues")
        cascade = spread(self.store.graph, cues.cues, self.cfg,
                         availability=availability_fn(self.store, self.cfg, now))
        timing.mark("spread")
        ws = compete(self.store, cascade, self.cfg, cues=cues, now=now)
        timing.mark("workspace")
        # Graph-only inference: microseconds, so it can ride along with every
        # turn. Model-backed inference never touches the critical path.
        attach_inferences(self.store, ws, self.cfg, now)
        timing.mark("infer")

        # The cascade, as it actually happened. The data structure that exists
        # so `you2 why` can print a path is already an animation.
        self.bus.state_changed(State.REMEMBERING)
        self.bus.publish("cascade", **viz.scene(self.store, ws, query=text).as_dict())

        reply = Reply(workspace=ws, timing=timing)

        # ---- repair, before anything else ----------------------------------
        # "No, that's out of date" must work in the moment. Deferring it to the
        # night job means arguing about the same thing twice.
        repair = reconsolidate.apply(self.store, self.llm, self._last_workspace,
                                     text, self.cfg, now)
        reply.repair = repair
        if repair.kind == "deleted":
            # Name the victim. The deletion is unrecoverable, so a bare
            # "Forgotten." gives the one person who could catch a mistake no
            # way to notice it -- and `repair.target` is holding the neuron
            # that was just destroyed.
            gone = repair.target.text() if repair.target is not None else ""
            reply.text = f"Forgotten: {gone}" if gone else "Forgotten."
            reply.route = "router"
            return self._finish(reply, turn, ws, now, prosody, source, skip_encode=True)
        if repair.kind == "corrected":
            reply.text = "Got it — updated."
            reply.route = "router"
            return self._finish(reply, turn, ws, now, prosody, source)
        timing.mark("repair")

        # ---- router and memory-answerable ----------------------------------
        # The exchange still in progress: said, and not yet written, because
        # an episode is only sealed when the next one starts.
        pending = self.segmenter.open
        recent = [t.text for t in pending.turns] if pending is not None else []
        routed: Routed = self.router.route(self.store, text, ws, self.cfg, now,
                                           recent=recent)
        timing.mark("route")
        if routed.handled:
            reply.text = routed.text
            reply.intent = routed.intent
            reply.route = "router" if not routed.intent.startswith("memory") else "memory"
            reply.cites = routed.cites
            # A device command is not an experience about the person. "What
            # time is it" was being stored as an episode, competing for
            # retrieval with things they actually told you about their life.
            # Memory questions still encode: what someone tries to recall is
            # itself evidence about what matters to them.
            return self._finish(reply, turn, ws, now, prosody, source,
                                skip_encode=routed.intent in DEVICE_INTENTS)

        # ---- the model -----------------------------------------------------
        self.bus.state_changed(State.THINKING)
        reply.text, reply.route = self._generate(ws, text)
        timing.mark("generate")
        return self._finish(reply, turn, ws, now, prosody, source)

    def stream(self, text: str, *, now: float | None = None) -> Iterator[str]:
        """Token deltas, for the TTS path. Never wait for generation to finish."""
        now = now if now is not None else self.clock.now()
        ws = self._retrieve(text, now)
        self._last_workspace = ws
        if self.llm is None:
            yield OFFLINE_FALLBACK
            return
        block = "\n".join(x for x in (self.persona, self.profile, ws.block()) if x)
        yield from self.llm.stream(prompts.answer(block, self._history[-HISTORY_TURNS:],
                                                  text))

    def _retrieve(self, text: str, now: float) -> Workspace:
        cues = build_cues(self.store, text, self.cfg, context=self._context, now=now)
        cascade = spread(self.store.graph, cues.cues, self.cfg,
                         availability=availability_fn(self.store, self.cfg, now))
        ws = compete(self.store, cascade, self.cfg, cues=cues, now=now)
        return attach_inferences(self.store, ws, self.cfg, now)

    def _generate(self, ws: Workspace, text: str) -> tuple[str, str]:
        if self.llm is None:
            return OFFLINE_FALLBACK, "offline"
        # Persona, then profile, then this turn's retrieval. The first two are
        # stable all day and stay inside the cacheable prefix; only the last
        # changes per turn.
        block = "\n".join(x for x in (self.persona, self.profile) if x)
        if ws.block():
            block = (block + "\n" + ws.block()).strip()
        try:
            out = self.llm.complete(
                prompts.answer(block, self._history[-HISTORY_TURNS:], text),
                temperature=0.4, max_tokens=220)
            return out.strip(), "model"
        except LLMUnavailable:
            self.store.log("offline", {"utterance": text[:120]})
            return OFFLINE_FALLBACK, "offline"

    # ---- everything below happens after the person has heard the answer ----

    def _finish(self, reply: Reply, turn, ws: Workspace, now: float,
                prosody: Prosody | None, source: str,
                *, skip_encode: bool = False) -> Reply:
        self.attention.reply_start(now)
        self.bus.state_changed(State.SPEAKING)
        self.bus.publish("reply", text=reply.text, route=reply.route,
                         ms=reply.timing.total_ms, tokens=ws.tokens)
        self._last_workspace = ws
        self._context = ws.context()
        self._history = (self._history + [{"role": "user", "content": turn.text},
                                          {"role": "assistant", "content": reply.text}]
                         )[-HISTORY_TURNS * 2:]

        broadcast(self.store, ws, self.cfg, now=now)
        reply.timing.mark("broadcast")

        if not skip_encode:
            arousal = None
            if prosody is not None:
                self.baseline.update(prosody, now)
                arousal = self.baseline.arousal(prosody)
            salience = evaluate(self.store, turn.text, self.cfg, workspace=ws,
                                arousal=arousal,
                                engagement=self.attention.engagement(),
                                source=source, now=now)
            reply.salience = salience
            closed = self.segmenter.feed(turn, salience)
            if closed is not None:
                out = encode(self.store, closed, self.cfg,
                             previous=self._previous_episode, source=source)
                reply.encoded = out
                if out.episode is not None:
                    self._previous_episode = out.episode.id
        reply.timing.mark("encode")

        self.attention.reply_end(now)
        self.bus.state_changed(State.LISTENING if self.attention.accepts(now)
                               else State.IDLE)
        self.store.log("turn", {"route": reply.route,
                                "timing": reply.timing.as_dict(),
                                "workspace": ws.ids(), "tokens": ws.tokens,
                                "salience": reply.salience.as_dict()
                                if reply.salience else None})
        # Commit the turn.
        #
        # sqlite3 opens an implicit deferred transaction on the first write,
        # and `store.flush()` was the only commit in the system — reached by
        # `Brain.close()`, `store.close()` and the night, none of which a turn
        # touches. Under the shipped unit (`you2 serve`, Restart=always, no
        # signal handler) every restart, reboot or OOM kill on a 2 GB Pi threw
        # away everything since the last consolidation. Measured: 60 turns
        # give 61 neurons and 181 edges in process, and **0 and 0** on disk
        # after a hard kill. The 05:30 backup timer then faithfully backed up
        # a store missing the day.
        #
        # After the reply and after the encode, so it costs the person
        # nothing: ~0.1 ms against a ~4 ms turn.
        self.store.flush()
        return reply

    # ---------------------------------------------------------------- night

    def night(self, *, now: float | None = None, interrupt=None,
              seed: int | None = None) -> night.NightReport:
        """Consolidate. Holds the brain for the duration, so a turn arriving
        mid-night waits rather than interleaving on the same connection."""
        with self._lock:
            return self._night(now=now, interrupt=interrupt, seed=seed)

    def _night(self, *, now: float | None = None, interrupt=None,
               seed: int | None = None) -> night.NightReport:
        now = now if now is not None else self.clock.now()
        self.close(now)
        self.bus.state_changed(State.DREAMING)
        try:
            report = night.run(self.store, self.llm, self.cfg, now=now,
                               interrupt=interrupt, seed=seed)
        finally:
            self.bus.state_changed(State.IDLE)
        self.profile = report.profile
        self.persona = report.persona
        self.bus.publish("night", **viz.night_frame(report, "done"))
        return report

    def load_profile(self, now: float | None = None) -> str:
        now = now or self.clock.now()
        self.profile = night.build_profile(self.store, self.cfg, now)
        from ..person import build as build_person
        self.persona = build_person(self.store, self.cfg, now).persona(now)
        return self.profile

    # ----------------------------------------------------------------- misc

    def why(self, text: str, *, now: float | None = None) -> str:
        with self._lock:
            now = now if now is not None else self.clock.now()
            return self._retrieve(text, now).explain()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return self._stats()

    def _stats(self) -> dict[str, Any]:
        out = self.store.stats()
        out["profile_tokens"] = len(self.profile) // 4
        out["precision"] = self.store.precision_by_type()
        return out

    def close_store(self) -> None:
        self.store.close()

    def __enter__(self) -> Brain:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close_store()

    def __repr__(self) -> str:
        return f"Brain({self.store!r})"

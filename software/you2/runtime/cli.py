"""`you2` — talk to it, sleep it, and see inside it.

The last one matters most during development. `you2 why "..."` prints the
whole retrieval: which cues fired, what the cascade visited, what won the
workspace and by what path, and what lateral inhibition suppressed. A memory
system you cannot inspect is one you cannot tune, and this is a system that
will be tuned for months.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

from ..clock import RealClock, SimClock, human, iso
from ..config import BrainConfig
from ..llm import LLM, FakeLLM
from ..substrate import Kind, Tier, get_embedder
from .pipeline import Brain

BANNER = """you2 — say something, or:
  /why <text>     what retrieval does with it      /night      run consolidation
  /stats          what is in there                 /profile    the daily block
  /open           open loops                       /new        insights waiting
  /forget <text>  delete a memory                  /quit
"""


def build(args: argparse.Namespace) -> Brain:
    cfg = BrainConfig.load(args.config, db_path=args.db) if args.config \
        else BrainConfig(db_path=args.db)
    llm = None
    if args.fake:
        llm = FakeLLM()
    elif os.environ.get("YOU2_API_KEY") or os.environ.get("GROQ_API_KEY"):
        llm = LLM(cfg)
    else:
        print("! no API key (YOU2_API_KEY / GROQ_API_KEY) — running offline.\n"
              "  The intent router and memory answers still work.\n",
              file=sys.stderr)
    clock = SimClock() if args.sim else RealClock()
    brain = Brain(cfg, clock=clock, llm=llm,
                  embedder=get_embedder(cfg.embed_dim, args.model_dir))
    brain.load_profile()
    return brain


# ------------------------------------------------------------------ commands

def cmd_chat(args: argparse.Namespace) -> int:
    brain = build(args)
    print(BANNER)
    print(f"  {brain.store.count()} neurons, {brain.store.graph.edge_count} synapses\n")
    try:
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line.startswith("/"):
                if _slash(brain, line):
                    continue
                break
            reply = brain.say(line)
            print(f"  {reply.text}")
            if args.verbose:
                print(f"  [{reply.route} {reply.timing.total_ms:.0f}ms "
                      f"{reply.workspace.tokens if reply.workspace else 0}tok "
                      f"sal={reply.salience.total if reply.salience else 0:.2f}]")
    finally:
        brain.close()
        brain.close_store()
    return 0


def _slash(brain: Brain, line: str) -> bool:
    cmd, _, rest = line.partition(" ")
    rest = rest.strip()
    if cmd == "/why":
        print(brain.why(rest or "what do you know about me"))
    elif cmd == "/night":
        report = brain.night()
        print(report.journal())
    elif cmd == "/stats":
        print(json.dumps(brain.stats(), indent=2, default=str))
    elif cmd == "/profile":
        print(brain.load_profile() or "(empty — run /night first)")
    elif cmd == "/open":
        now = brain.clock.now()
        loops = [n for n in brain.store.by_kind(Kind.LOOP, limit=50)
                 if n.invalid_at is None]
        for n in loops:
            print(f"  #{n.id} {n.content}")
        if not loops:
            print("  nothing open")
    elif cmd == "/new":
        from ..discovery import pending
        for n in pending(brain.store, limit=10):
            print(f"  #{n.id} [{n.meta.get('type')}] {n.content}")
    elif cmd == "/forget":
        hits = brain.store.search(rest, k=1)
        if not hits:
            print("  nothing matched")
        else:
            removed = brain.store.entomb(hits[0][0])
            print(f"  removed {len(removed)} neurons")
    else:
        print(f"  unknown: {cmd}")
    return True


def cmd_night(args: argparse.Namespace) -> int:
    brain = build(args)
    report = brain.night()
    print(report.journal())
    brain.close_store()
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    brain = build(args)
    print(brain.why(" ".join(args.text)))
    brain.close_store()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    brain = build(args)
    stats = brain.stats()
    from ..consolidation.forgetting import growth_curve
    from ..inference.compile import stats as compile_stats
    stats["growth"] = growth_curve(brain.store, brain.cfg)
    stats["inference"] = compile_stats(brain.store)
    from ..consolidation.storage import breakdown, measure
    stats["storage"] = measure(brain.store).as_dict()
    print(json.dumps(stats, indent=2, default=str))
    print()
    print(breakdown(brain.store))
    brain.close_store()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Bring up the face: the eye, the light bar, and the night timer."""
    return _serve(build(args), args)


def _serve(brain: Brain, args: argparse.Namespace,
           *, open_browser: bool = False) -> int:
    from .events import BUS, State
    from .leds import Bar
    from .server import serve

    bar = Bar()
    bar.start(BUS)
    httpd = serve(brain, host=args.host, port=args.port, background=True)

    where = f"http://{args.host}:{args.port}"
    print(f"you2 — {brain.store.count()} neurons, "
          f"{brain.store.graph.edge_count} synapses")
    print(f"  eye:   {where}")
    print(f"  light: {'/dev/leds0' if bar.live else 'no device (fine off a Pi)'}")
    if args.host != "127.0.0.1":
        print("  ! bound beyond loopback — this server carries the memory store")
    BUS.state_changed(State.IDLE)
    if open_browser:
        import webbrowser
        webbrowser.open(where)

    stop = threading.Event()
    if args.night:
        threading.Thread(target=_night_loop, args=(brain, stop), daemon=True,
                         name="you2-night").start()
    try:
        stop.wait()
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        httpd.shutdown()
        bar.stop()
        brain.close()
        brain.close_store()
    return 0


def _night_loop(brain: Brain, stop: threading.Event) -> None:
    """Consolidate when the window opens, once a night.

    Polled rather than scheduled: a cron entry cannot see whether someone is
    mid-conversation, and the night must yield to a wake word.
    """
    from ..consolidation.night import due

    while not stop.wait(120.0):
        try:
            if due(brain.store, brain.cfg, brain.clock.now()):
                brain.night(interrupt=lambda: brain.attention.accepts())
        except Exception as exc:                       # noqa: BLE001
            brain.store.log("night_failed", {"error": str(exc)[:300]})


#: A month of plausible life, so the eye has something to light up on the
#: first run. Chosen to exercise every mechanism the visualisation shows:
#: entities that recur, a preference that changes, a commitment that closes,
#: and one that does not.
DEMO = [
    "I had lunch with David Anderson about the Kestrel contract",
    "I'll send him the revised numbers by Friday",
    "the Kestrel migration keeps slipping, we are still waiting on their test team",
    "my sister Priya is moving to Lisbon in the spring",
    "I really cannot stand cilantro, it ruins everything",
    "I prefer meetings in the morning, I am useless after three",
    "the garden wall needs rebuilding before winter",
    "Marta Nilsen is drawing up plans for the garden wall",
    "the garden wall keeps slipping too, still waiting on Marta's drawings",
    "sent David Anderson the revised numbers, that is done",
    "remember that the boiler service is due in October",
    "the Tuesday planning meeting left me completely drained again",
    "I need to book the boiler service",
    "actually I prefer meetings in the afternoon these days",
    "the Tuesday planning meeting wiped me out again, awful",
]


def seed_demo(brain: Brain) -> object:
    """Live through the demo month. Returns the last night's report."""
    from ..clock import DAY

    clock = brain.clock
    report = None
    for i, text in enumerate(DEMO):
        clock.advance(seconds=2.0 * DAY if i % 3 == 0 else 1800.0)
        brain.say(text, now=clock.now())
        if i % 4 == 3:
            brain.close(clock.now())
            clock.advance(hours=9)
            report = brain.night(now=clock.now(), seed=1)
    brain.close(clock.now())
    clock.advance(hours=9)
    return brain.night(now=clock.now(), seed=1)


def cmd_demo(args: argparse.Namespace) -> int:
    """Seed a month of life, consolidate it, and open the eye.

    Exists because a memory device with an empty memory demonstrates nothing,
    and the first thirty seconds is where anyone decides whether this is
    interesting at all.
    """
    args.fake = True
    args.sim = True
    brain = build(args)
    print(f"seeding {len(DEMO)} exchanges over a month…\n")
    report = seed_demo(brain)
    print(report.journal())
    print()
    brain.close_store()

    # The eye shows a real clock, so the server runs on the real one.
    args.sim = False
    args.night = False
    return _serve(build(args), args, open_browser=True)


def cmd_listen(args: argparse.Namespace) -> int:
    """The whole device: ears, brain, voice.

    With a microphone it is the real loop. Without one it reads typed lines
    and synthesises audio with the right shape, so the VAD, the endpointer,
    the dwell window and barge-in all behave exactly as they will on hardware.
    """
    from ..audio import ScriptedASR, converse, make, typed_frames
    from .events import BUS, State

    brain = build(args)
    brain.load_profile()
    listener = make(brain.cfg, bus=brain.bus)
    stages = getattr(listener, "stages", {})

    print(f"wake word: \"{brain.cfg.wake_phrase}\"")
    for name, how in stages.items():
        print(f"  {name:<5} {how}")
    print()

    source = None
    if args.mic:
        try:
            from ..audio import SoundDevice
            source = SoundDevice(device=args.device)
            source.start()
            listener.playout = source.play
            frames = source.frames()
            print("listening on the microphone. Ctrl-C to stop.")
        except Exception as exc:                       # noqa: BLE001
            print(f"! no microphone ({exc}); falling back to typed input\n",
                  file=sys.stderr)
            source = None
    if source is None:
        if not isinstance(listener.asr, ScriptedASR):
            listener.asr = ScriptedASR()
        frames = typed_frames(listener.asr,
                              prompt=f"[say \"{brain.cfg.wake_phrase}\"] > ")

    def report(turn, reply):
        print(f"  {reply.text}")
        if args.verbose:
            print(f"  [{reply.route} {reply.timing.total_ms:.0f}ms "
                  f"{reply.workspace.tokens if reply.workspace else 0}tok "
                  f"woken={turn.woken}]")

    try:
        converse(brain, listener, frames, on_reply=report)
    except KeyboardInterrupt:
        print()
    finally:
        if source is not None:
            source.stop()
        brain.close()
        brain.close_store()
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from ..eval import ablate, compare, run
    if args.ablate:
        results = ablate(days=args.days, seed=args.seed)
        print(compare(results))
    else:
        result = run(days=args.days, seed=args.seed, quiet=False)
        print(result.table())
        print()
        print(result.summary())
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Score extraction against the hand-labelled corpus.

    Separate from `eval` because it answers a different question. `eval` asks
    whether retrieval holds at month 24; this asks whether what went in was
    right in the first place, which no amount of consolidation can fix.
    """
    from ..eval import extraction
    from ..eval.transcripts import totals

    if args.text:
        return _extract_one(" ".join(args.text))

    llm = None
    label = "model"
    if args.live:
        if not (os.environ.get("YOU2_API_KEY") or os.environ.get("GROQ_API_KEY")):
            print("! --live needs YOU2_API_KEY or GROQ_API_KEY", file=sys.stderr)
            return 2
        cfg = BrainConfig(db_path=":memory:")
        llm = LLM(cfg)
        label = cfg.llm_model
    elif args.fake:
        llm = FakeLLM()
        label = "fake"

    counts = totals()
    print(f"{counts['sessions']} sessions, {counts['utterances']} utterances, "
          f"{counts['facts']} labelled facts, {counts['traps']} traps, "
          f"{counts['stability_pairs']} dimensions stated twice\n")

    scores = extraction.suite(llm)
    if llm is not None:
        scores[-1].label = label
    print(extraction.compare(scores))
    if args.report:
        print()
        for s in scores:
            print(s.report())
            print()
    return 0 if scores[1].clean else 1


def _extract_one(text: str) -> int:
    """What would this utterance put in the store? The fastest loop there is
    for working on extraction."""
    from ..llm import rules, validate

    raw = rules.extract([text], [1], 0.0).as_dict()
    checked = validate.clean(raw, sources={1: text})
    print(f"“{text}”\n")
    for f in checked.facts:
        print(f"  fact    {f['key']:<28} {f['text']}")
    for e in checked.entities:
        print(f"  entity  {e['name']} ({e['kind']})")
    for loop in checked.loops:
        print(f"  loop    {loop['text']}")
    for c in checked.closed:
        print(f"  closes  {c}")
    for r in checked.rejected:
        print(f"  dropped {r.text[:52]!r} — {r.reason}")
    if not any((checked.facts, checked.entities, checked.loops,
                checked.closed, checked.rejected)):
        print("  nothing — which is the right answer for most turns")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Ingest a folder of markdown. Documents are salience-discounted."""
    brain = build(args)
    root = Path(args.path)
    files = sorted(root.rglob("*.md")) if root.is_dir() else [root]
    n = 0
    for path in files:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for para in (p.strip() for p in text.split("\n\n")):
            if len(para) < 40:
                continue
            # Split rather than truncate. The encoder caps an episode as a
            # safety net, but a long paragraph's later sentences are real
            # content and a vault import should not lose them — the cap is
            # there to stop a 102 KB neuron, not to discard the document.
            for chunk in _chunks(para, brain.cfg.max_episode_chars):
                brain.say(chunk, source="document")
                n += 1
    brain.close()
    print(f"read {len(files)} files, {n} passages")
    brain.close_store()
    return 0


def _chunks(text: str, limit: int) -> list[str]:
    """A long passage, split on sentence ends, each piece under `limit`."""
    if len(text) <= limit:
        return [text]
    import re as _re
    out: list[str] = []
    buf = ""
    for piece in _re.split(r"(?<=[.!?])\s+", text):
        if buf and len(buf) + len(piece) + 1 > limit:
            out.append(buf.strip())
            buf = ""
        # A single sentence longer than the limit is pathological; the
        # encoder's cap catches it and says so.
        buf = f"{buf} {piece}".strip() if buf else piece
    if buf.strip():
        out.append(buf.strip())
    return out


def _global_flags(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--db", default="data/brain.db")
    parser.add_argument("--config", default=None)
    parser.add_argument("--model-dir", default=os.environ.get("YOU2_MODEL_DIR"))
    parser.add_argument("--fake", action="store_true",
                        help="deterministic stand-in model, no network")
    parser.add_argument("--sim", action="store_true",
                        help="simulated clock, for experiments")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    # The global flags are attached to every subcommand as well as to the
    # top level, so both `you2 --fake chat` and `you2 chat --fake` work.
    # argparse puts subcommand args after the verb by default, and `you2 chat
    # --fake` is what anyone actually types.
    common = _global_flags(argparse.ArgumentParser(add_help=False))
    parser = _global_flags(argparse.ArgumentParser("you2", description="the brain"))
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("chat", parents=[common]).set_defaults(fn=cmd_chat)
    sub.add_parser("night", parents=[common]).set_defaults(fn=cmd_night)
    sub.add_parser("stats", parents=[common]).set_defaults(fn=cmd_stats)

    sv = sub.add_parser("serve", parents=[common],
                        help="the eye, the light bar, and the night timer")
    sv.add_argument("--host", default="127.0.0.1",
                    help="loopback by default: this server carries the memory store")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-night", dest="night", action="store_false",
                    help="do not consolidate automatically")
    sv.set_defaults(fn=cmd_serve, night=True)

    ls = sub.add_parser("listen", parents=[common],
                        help="the whole device: ears, brain, voice")
    ls.add_argument("--mic", action="store_true", help="use a real microphone")
    ls.add_argument("--device", default=None, help="sound device name or index")
    ls.set_defaults(fn=cmd_listen)

    dm = sub.add_parser("demo", parents=[common],
                        help="seed a month of life and open the eye")
    dm.add_argument("--host", default="127.0.0.1")
    dm.add_argument("--port", type=int, default=8765)
    dm.set_defaults(fn=cmd_demo, night=False)

    why = sub.add_parser("why", parents=[common])
    why.add_argument("text", nargs="+")
    why.set_defaults(fn=cmd_why)

    ev = sub.add_parser("eval", parents=[common])
    ev.add_argument("--days", type=int, default=365)
    ev.add_argument("--seed", type=int, default=0)
    ev.add_argument("--ablate", action="store_true")
    ev.set_defaults(fn=cmd_eval)

    ex = sub.add_parser("extract", parents=[common],
                        help="score extraction against labelled transcripts")
    ex.add_argument("text", nargs="*",
                    help="score one utterance instead of the corpus")
    ex.add_argument("--live", action="store_true",
                    help="also run the real model (needs an API key)")
    ex.add_argument("--report", action="store_true",
                    help="every miss, trap and unasked-for claim")
    ex.set_defaults(fn=cmd_extract)

    imp = sub.add_parser("import", parents=[common])
    imp.add_argument("path")
    imp.set_defaults(fn=cmd_import)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        args.fn = cmd_chat
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

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
    print(json.dumps(stats, indent=2, default=str))
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
            brain.say(para, source="document")
            n += 1
    brain.close()
    print(f"read {len(files)} files, {n} passages")
    brain.close_store()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser("you2", description="the brain")
    parser.add_argument("--db", default="data/brain.db")
    parser.add_argument("--config", default=None)
    parser.add_argument("--model-dir", default=os.environ.get("YOU2_MODEL_DIR"))
    parser.add_argument("--fake", action="store_true",
                        help="deterministic stand-in model, no network")
    parser.add_argument("--sim", action="store_true",
                        help="simulated clock, for experiments")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("chat").set_defaults(fn=cmd_chat)
    sub.add_parser("night").set_defaults(fn=cmd_night)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)

    why = sub.add_parser("why")
    why.add_argument("text", nargs="+")
    why.set_defaults(fn=cmd_why)

    ev = sub.add_parser("eval")
    ev.add_argument("--days", type=int, default=365)
    ev.add_argument("--seed", type=int, default=0)
    ev.add_argument("--ablate", action="store_true")
    ev.set_defaults(fn=cmd_eval)

    imp = sub.add_parser("import")
    imp.add_argument("path")
    imp.set_defaults(fn=cmd_import)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        args.fn = cmd_chat
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

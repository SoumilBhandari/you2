"""The whole loop: hear -> recall -> route -> answer -> broadcast -> encode."""

from __future__ import annotations

import re

import pytest

from you2 import BrainConfig, SimClock
from you2.llm import FakeLLM, LLMUnavailable, sentences
from you2.perception import Prosody
from you2.recall import reconsolidate
from you2.runtime import Brain
from you2.substrate import HashEmbedder, Kind


@pytest.fixture
def brain(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "brain.db"))
    b = Brain(cfg, clock=SimClock(), llm=FakeLLM(),
              embedder=HashEmbedder(dim=cfg.embed_dim))
    yield b
    b.close_store()


def talk(brain, lines, gap=30.0):
    out = []
    for line in lines:
        out.append(brain.say(line, now=brain.clock.now()))
        brain.clock.advance(seconds=gap)
    return out


# ------------------------------------------------------------------ the turn

def test_a_turn_runs_end_to_end(brain):
    reply = brain.say("I had lunch with David Anderson about the Kestrel contract")
    assert reply.text
    assert reply.route in ("model", "memory", "router")
    assert reply.workspace is not None
    assert reply.salience is not None
    assert "total" in reply.timing.as_dict()


def test_every_stage_is_timed(brain):
    """When you are over budget the log has to say which stage to attack,
    instead of sending you board-shopping."""
    reply = brain.say("the retaining wall needs rebuilding before winter")
    stages = reply.timing.as_dict()
    for stage in ("attend", "cues", "spread", "workspace", "infer", "broadcast"):
        assert stage in stages, stages


def test_the_router_answers_without_the_model(brain):
    before = len(brain.llm.seen)
    reply = brain.say("what time is it")
    assert reply.route == "router"
    assert len(brain.llm.seen) == before, "the router must not call the model"


def test_memory_answers_without_the_model(brain):
    talk(brain, ["I'll email David Anderson about the contract by Friday"])
    brain.close()                     # flush the open episode so the loop exists
    before = len(brain.llm.seen)
    reply = brain.say("what am I working on")
    assert reply.route == "memory"
    assert "David Anderson" in reply.text or "email" in reply.text
    assert len(brain.llm.seen) == before


def test_offline_still_answers_from_memory(tmp_path):
    """No network means 'slightly less capable', not a brick."""
    cfg = BrainConfig(db_path=str(tmp_path / "b.db"))

    class Dead:
        usage = None

        def complete(self, *a, **k):
            raise LLMUnavailable("no network")

        def structured(self, *a, **k):
            raise LLMUnavailable("no network")

    b = Brain(cfg, clock=SimClock(), llm=Dead(),
              embedder=HashEmbedder(dim=cfg.embed_dim))
    b.say("I need to book the boiler service")
    assert b.say("what time is it").route == "router"
    assert b.say("what am I working on").route == "memory"
    assert b.say("tell me a story").route == "offline"
    b.close_store()


def test_a_turn_ignored_when_dormant_and_not_woken(brain):
    brain.attention.close(brain.clock.now())
    reply = brain.say("something said across the room", wake=False)
    assert reply.route == "ignored"
    assert brain.store.count(Kind.EPISODE) == 0


# ------------------------------------------------------------- the workspace

def test_the_block_never_exceeds_the_budget_through_the_pipeline(brain):
    talk(brain, [f"I keep thinking about topic {i} and the garden wall"
                 for i in range(25)], gap=400.0)
    brain.close()
    reply = brain.say("what about the garden wall")
    assert reply.workspace.tokens <= brain.cfg.workspace_tokens


def test_context_carries_across_turns(brain):
    talk(brain, ["the retaining wall at the back needs rebuilding before winter"],
         gap=400.0)
    brain.close()
    first = brain.say("what about the retaining wall")
    brain.clock.advance(seconds=4)
    second = brain.say("how much would that cost")
    assert second.workspace is not None
    assert set(first.workspace.ids()) & set(second.workspace.ids())


# ------------------------------------------------------------ reconsolidation

def test_a_correction_lands_immediately_not_at_3am(brain):
    """The single most important trust interaction the device has. Deferring
    it to the night job means arguing about the same thing twice."""
    from you2.consolidation.supersede import resolve

    from you2.substrate import Neuron
    episode = brain.store.add(Neuron(kind=Kind.EPISODE, content="I work at Kestrel"))
    fact = resolve(brain.store, None, "user/employer", "the user works at Kestrel",
                   [episode.id], 0.9, brain.clock.now(), brain.cfg).fact

    brain.clock.advance(days=30)
    brain.say("where do I work")
    reply = brain.say("no, that's out of date, I'm at Lumen now")

    assert reply.repair is not None and reply.repair.kind == "corrected"
    assert brain.store.get(fact.id).invalid_at is not None
    live = brain.store.by_key("user/employer")
    assert live is not None and live.id != fact.id
    assert live.pinned, "a user correction is authoritative"


def test_forget_that_removes_it_and_its_conclusions(brain):
    from you2.substrate import Neuron
    brain.store.add(Neuron(kind=Kind.EPISODE,
                           content="I really cannot stand cilantro"))
    brain.say("what do I think about cilantro")
    reply = brain.say("forget that")
    assert reply.repair.kind == "deleted"
    # Naming the victim is the point: the delete is unrecoverable, so the one
    # person who could catch a mistake needs to be told what went.
    assert reply.text.startswith("Forgotten: ")
    assert "cilantro" in reply.text


def test_correction_detection_does_not_fire_on_ordinary_speech():
    for text in ("that's a good idea", "I think it moved to September",
                 "actually pretty good"):
        assert not reconsolidate.looks_like_deletion(text)


def test_a_turn_of_phrase_is_not_an_instruction_to_delete():
    """The unanchored version of this pattern matched ten plain sentences out
    of ten and nine were wrong -- and the branch behind it is the only
    unrecoverable operation in the system."""
    for text in ("I'll never forget that lunch with David Anderson",
                 "don't forget that the bins go out tomorrow",
                 "I won't forget that you helped me",
                 "we should delete that column in the spreadsheet at work",
                 "I hope you don't forget about that dentist appointment",
                 "remind me to delete that file tomorrow",
                 "she said to forget about that whole business",
                 "I can never forget what I said to her",
                 "I'm trying not to forget that appointment"):
        assert not reconsolidate.looks_like_deletion(text), text

    # ...while the instruction itself still lands, including after a clause
    # boundary and behind the politeness people actually use.
    for text in ("forget that", "forget about that", "delete that",
                 "never mind that", "please forget that",
                 "ok forget what I said", "that was wrong. forget that."):
        assert reconsolidate.looks_like_deletion(text), text


# -------------------------------------------------------------------- prosody

def test_prosody_feeds_salience_through_the_pipeline(brain):
    import numpy as np
    sr = 16000
    t = np.arange(sr) / sr
    loud = (0.6 * np.sin(2 * np.pi * 190 * t)).astype(np.float32)
    from you2.perception import extract

    for _ in range(35):                      # establish a calm baseline
        brain.baseline.update(Prosody(rms_mean=0.08, f0_mean=115.0, f0_var=30.0,
                                      speech_rate=3.0, pause_ratio=0.4),
                              brain.clock.now())
        brain.clock.advance(seconds=60)

    reply = brain.say("the deadline moved again and nobody told me",
                      prosody=extract(loud, sr))
    assert reply.salience is not None
    assert reply.salience.arousal > 0.5


# --------------------------------------------------------------------- night

def test_a_night_through_the_brain_object(brain):
    talk(brain, [
        "I had lunch with David Anderson about the Kestrel contract",
        "I really cannot stand cilantro, it ruins everything",
        "I'll send him the revised numbers by Friday",
        "my sister is moving to Lisbon in the spring",
    ], gap=600.0)
    brain.clock.advance(hours=8)
    report = brain.night(seed=3)

    assert report.episodes_replayed >= 2
    assert brain.profile == report.profile
    assert report.audit_failures == []
    assert "storage" in report.as_dict()
    assert report.storage["total_mb"] >= 0


def test_the_profile_is_prepended_to_the_prompt(brain):
    talk(brain, ["I am allergic to penicillin", "I live in Bristol"], gap=600.0)
    brain.clock.advance(hours=8)
    brain.night(seed=1)
    assert brain.profile

    brain.llm.seen.clear()
    brain.say("tell me something about myself")
    systems = [m["content"] for msgs in brain.llm.seen for m in msgs
               if m["role"] == "system"]
    assert any(brain.profile.splitlines()[0] in s for s in systems if s)


# ----------------------------------------------------------------- streaming

def test_sentence_chunking_yields_speakable_clauses():
    deltas = iter(["The Anderson ", "contract ", "moved to September. ",
                   "I can chase ", "it on Monday. ", "Shall I?"])
    out = list(sentences(deltas))
    assert len(out) >= 2
    assert out[0].endswith(".")
    assert all(len(s.strip()) > 0 for s in out)


def test_why_explains_a_retrieval(brain):
    talk(brain, ["I had lunch with David Anderson about the Kestrel contract"])
    text = brain.why("how is Anderson")
    assert "cues:" in text and "cascade:" in text


def test_device_commands_are_not_stored_as_experiences(brain):
    """"What time is it" was being stored as an episode, competing for
    retrieval with things the person actually said about their life."""
    brain.say("what time is it")
    brain.say("set a timer for 10 minutes")
    brain.close()
    assert brain.store.count(Kind.EPISODE) == 0

    # A memory question is different: what someone tries to recall is itself
    # evidence about what matters to them.
    brain.say("I had lunch with David Anderson about the Kestrel contract")
    brain.close()
    assert brain.store.count(Kind.EPISODE) == 1


def test_the_memory_path_refuses_a_topic_it_has_never_heard_of(brain):
    """Retrieval always returns *something* — that is what spreading
    activation does, and ambient cues alone guarantee a non-empty workspace.
    So "resolvable from the workspace alone" quietly became "answer with
    whatever is in the workspace", and asking about a project this store had
    never heard of produced a confident sentence about the user's sister and
    cilantro.

    Confabulation is the worst failure this device has: stated in the user's
    own vocabulary, with no model involved to hedge it, on the fastest path
    in the system. "Nothing on that" is the correct answer, and it is what
    makes every other answer worth believing.
    """
    for said in ("the garden wall needs rebuilding before winter",
                 "I really cannot stand cilantro",
                 "my sister Priya is moving to Lisbon"):
        brain.say(said)
    brain.close()

    miss = brain.say("what did I say about the Halloway report")
    assert "nothing" in miss.text.lower(), miss.text
    assert "cilantro" not in miss.text.lower()
    assert "priya" not in miss.text.lower()

    hit = brain.say("what did I say about the garden wall")
    assert "wall" in hit.text.lower(), hit.text


def test_a_question_with_no_topic_still_answers_generally(brain):
    """The guard is on named topics. "What did I say" names nothing, so
    there is nothing to be off-topic about and it must not be refused."""
    brain.say("I really cannot stand cilantro")
    brain.close()
    out = brain.say("what did I say")
    assert "nothing" not in out.text.lower(), out.text
    assert "cilantro" in out.text.lower(), out.text


def test_it_can_answer_about_something_said_thirty_seconds_ago(brain):
    """An episode is a bounded exchange and is only sealed when the next one
    starts, so the most recent thing said is not in the store yet. The device
    answered "nothing about plumber" one turn after being told about the
    plumber — the first thing anyone tries, and the last thing a memory
    device should fail at.

    The fix is a working-memory buffer in the ordinary sense: small, current,
    consulted before long-term storage, and gone once its contents have been
    consolidated properly.
    """
    brain.say("I need to call the plumber tomorrow")
    out = brain.say("what did I say about the plumber")
    assert "plumber" in out.text.lower(), out.text
    assert "nothing" not in out.text.lower()


def test_working_memory_does_not_answer_for_a_topic_nobody_mentioned(brain):
    """It must not become a second route to confabulation."""
    brain.say("I need to call the plumber tomorrow")
    out = brain.say("what did I say about the Tokyo trip")
    assert "nothing" in out.text.lower(), out.text


def test_naming_the_thing_beats_being_connected_to_it(brain):
    """Graph adjacency is what makes "spoke to Priya about it for a while"
    answer a question about the Kestrel migration, and it is indispensable
    for that. But on a small store nearly everything is connected to nearly
    everything, so used alongside direct matches it let a question about
    Lisbon return the boiler service. Adjacency is the fallback, not a peer.
    """
    # Spaced, so each becomes its own episode. Said in one breath they are
    # one episode and answering about either correctly surfaces both.
    for said in ("my sister Priya is moving to Lisbon in the spring",
                 "the boiler service is due in October",
                 "the garden wall needs rebuilding before winter"):
        brain.say(said, now=brain.clock.now())
        brain.clock.advance(hours=6)
    brain.close(brain.clock.now())
    out = brain.say("what did I say about Lisbon", now=brain.clock.now())
    assert "lisbon" in out.text.lower(), out.text
    assert "boiler" not in out.text.lower(), out.text


def test_a_turn_survives_a_hard_kill(tmp_path):
    """`sqlite3` opens an implicit deferred transaction on the first write,
    and `store.flush()` was the only commit in the system — reached by
    `Brain.close()`, `store.close()` and the night, none of which a turn
    touches. Under the shipped unit (`you2 serve`, Restart=always) every
    restart, reboot or OOM kill on a 2 GB Pi threw away everything since the
    last consolidation.

    Measured before the fix: 60 turns gave 61 neurons and 181 edges in
    process, and **0 and 0** on disk after `os._exit(9)`. The 05:30 backup
    timer then faithfully backed up a store missing the day.

    A subprocess, because nothing short of a real kill proves it: `close()`,
    `__del__` and `atexit` would each hide the bug.
    """
    import subprocess
    import sys
    import textwrap

    db = tmp_path / "kill.db"
    script = textwrap.dedent(f"""
        import os
        from you2 import BrainConfig, SimClock
        from you2.runtime.pipeline import Brain
        from you2.llm import FakeLLM
        from you2.substrate import HashEmbedder, Kind
        cfg = BrainConfig(db_path={str(db)!r})
        b = Brain(cfg, clock=SimClock(), llm=FakeLLM(),
                  embedder=HashEmbedder(dim=cfg.embed_dim))
        for i in range(20):
            b.clock.advance(minutes=7)
            b.say(f"thing {{i}} about the garden wall", now=b.clock.now())
        print(b.store.count(Kind.EPISODE), flush=True)
        os._exit(9)
    """)
    # No check=True: os._exit(9) is the point of the test.
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True)
    assert out.returncode == 9, (out.returncode, out.stderr[-800:])
    in_process = int(out.stdout.strip().splitlines()[-1])
    assert in_process > 0

    from you2.substrate import Store

    cfg = BrainConfig(db_path=str(db))
    store = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
    # The still-open episode is legitimately lost — only close() seals it —
    # so this is >=, not ==. Do not tighten it without re-reading that.
    assert store.count(Kind.EPISODE) >= in_process - 1, (
        store.count(Kind.EPISODE), in_process)
    assert store.graph.edge_count > 0
    store.close()


def test_forgotten_means_forgotten_after_a_kill(tmp_path):
    """The device says "Forgotten." out loud, and `entomb` is documented as
    the only unrecoverable state — but it did not commit either, so after a
    kill the row was back at tier='hot' with its content verbatim.

    And the cascade has to reach the entity: an entity is carried by memories
    that are *about* it, not by ones that merely co-fired with it in a
    workspace. Counting ASSOC edges as carriers meant that simply *asking*
    about a memory before deleting it wired an unrelated episode to its
    entity and kept the name alive.
    """
    import re
    import subprocess
    import sys
    import textwrap

    db = tmp_path / "forget.db"
    script = textwrap.dedent(f"""
        import os
        from you2 import BrainConfig, SimClock
        from you2.runtime.pipeline import Brain
        from you2.llm import FakeLLM
        from you2.substrate import HashEmbedder
        cfg = BrainConfig(db_path={str(db)!r})
        b = Brain(cfg, clock=SimClock(), llm=FakeLLM(),
                  embedder=HashEmbedder(dim=cfg.embed_dim))
        b.say("my HIV test came back positive last Tuesday", now=b.clock.now())
        b.clock.advance(minutes=40)
        b.say("the garden wall needs rebuilding", now=b.clock.now())
        b.close(b.clock.now())
        b.clock.advance(hours=9)
        b.night(now=b.clock.now(), seed=1)
        b.clock.advance(hours=2)
        b.say("what did I say about the HIV test", now=b.clock.now())
        r = b.say("forget that", now=b.clock.now())
        assert r.repair is not None and r.repair.kind == "deleted", r.repair
        os._exit(9)
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True)
    assert out.returncode == 9, (out.returncode, out.stderr[-800:])

    from you2.substrate import Store

    cfg = BrainConfig(db_path=str(db))
    store = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
    leak = re.compile(r"\bhiv\b|\bpositive\b", re.IGNORECASE)
    tables = [r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        for row in store.conn.execute(f"SELECT * FROM {table}"):
            assert not leak.search(str(tuple(row))), (table, tuple(row))

    # And nothing else went with it.
    assert any("garden wall" in (n.content or "")
               for n in store.by_kind(Kind.EPISODE, limit=9))
    store.close()


# ------------------------------------------------------- configuration is real

#: Fields that are deliberately declared and not yet read. Each one is a
#: promise the code does not keep, so each needs a reason and a home in
#: docs/next.md — not a quiet pass.
UNWIRED_CONFIG: set[str] = set()


def test_every_config_field_is_read_somewhere():
    """A knob that nothing reads is a lie in the shape of an API.

    This sweep found five: journal_path (a file-based journal replaced by a
    SQLite table), retrieval_strength_init (retrieval strength is computed,
    not stored, so there was nothing to initialise), interrupt_check_seconds
    (a polling interval replaced by a callback), llm_night_model (documented
    as "consolidation can run a smaller, cheaper model" and never passed to
    one), and barge_in. Three were deleted and two were wired up, so the
    allowlist is now empty -- which is the state to keep it in.

    docs/next.md asked for this test. Writing it is cheaper than the next
    five.
    """
    import ast
    import pathlib

    pkg = pathlib.Path(__file__).resolve().parent.parent / "you2"
    cfg_path = pkg / "config.py"

    fields = {
        node.target.id
        for node in ast.walk(ast.parse(cfg_path.read_text()))
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and not node.target.id.startswith("_")
    }
    assert len(fields) > 50, "the config parser found almost nothing — it broke"

    elsewhere = "\n".join(
        p.read_text(errors="replace")
        for p in pkg.rglob("*.py") if p != cfg_path
    )
    read = {f for f in fields
            if re.search(rf"(?<![\w]){re.escape(f)}(?![\w])", elsewhere)}

    unread = fields - read - UNWIRED_CONFIG
    assert not unread, (
        f"config fields nothing reads: {sorted(unread)} — wire each one up, or "
        f"delete it, or add it to UNWIRED_CONFIG with the reason")

    stale = UNWIRED_CONFIG - fields
    assert not stale, (
        f"UNWIRED_CONFIG names fields that no longer exist: {sorted(stale)}")

    wired = UNWIRED_CONFIG & read
    assert not wired, (
        f"{sorted(wired)} is wired up now — take it out of UNWIRED_CONFIG")

"""The whole loop: hear -> recall -> route -> answer -> broadcast -> encode."""

from __future__ import annotations

import pytest

from you2 import DAY, BrainConfig, SimClock
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
    assert reply.text == "Forgotten."


def test_correction_detection_does_not_fire_on_ordinary_speech():
    for text in ("that's a good idea", "I think it moved to September",
                 "actually pretty good"):
        assert not reconsolidate.looks_like_deletion(text)


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

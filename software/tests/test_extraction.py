"""Extraction: is what goes into the store correct.

The year-long harness measures whether retrieval holds. It cannot see this,
because it feeds the rule extractor a corpus the same rules generated. These
tests are the other half, and they assert *claims* rather than numbers, so
tuning a pattern does not fail them but removing a defence does.

The thing being defended against throughout is the eager extractor. A memory
that fills with "the user is running late" is worse than an empty one: every
one of those is a distractor at retrieval time for as long as the device
lives, and unlike a missing fact it is never noticed.
"""

from __future__ import annotations

import pytest

from you2.eval import extraction
from you2.eval.sloppy import SloppyLLM
from you2.eval.transcripts import CORPUS, stability_pairs, totals
from you2.llm import rules, validate
from you2.perception.discourse import commitments


@pytest.fixture(scope="module")
def scored():
    return extraction.run(extraction.rule_extractor, label="rules")


@pytest.fixture(scope="module")
def sloppy_raw():
    return extraction.run(extraction.model_extractor(SloppyLLM()),
                          label="sloppy raw", validated=False)


@pytest.fixture(scope="module")
def sloppy_clean():
    return extraction.run(extraction.model_extractor(SloppyLLM()),
                          label="sloppy")


# ------------------------------------------------------------------ the corpus

def test_the_corpus_has_teeth():
    """A benchmark with no traps and no repeated dimensions measures recall
    and nothing else, which is the easy half."""
    counts = totals()
    assert counts["facts"] >= 20
    assert counts["traps"] >= 20
    assert counts["stability_pairs"] >= 3
    assert all(s.turns for s in CORPUS)


def test_every_stability_pair_is_genuinely_reworded():
    """If the two statements share their wording, agreeing on a key is a
    string match rather than an extraction result."""
    from you2.substrate.embed import tokenize
    for dim in stability_pairs():
        said = [f.must for s in CORPUS for f in s.facts if f.dim == dim]
        words = [set(tokenize(m)) for m in said]
        for i, a in enumerate(words):
            for b in words[i + 1:]:
                assert not (a <= b or b <= a), f"{dim} restates itself"


# ------------------------------------------------------------------- the rules

def test_nothing_the_labels_forbid_is_ever_stored(scored):
    assert scored.trap_hits == []


def test_no_two_dimensions_share_a_key(scored):
    """The failure that stability alone cannot see: mapping everything to one
    key would score perfectly and destroy the store."""
    assert scored.collisions == []


def test_recall_and_precision_hold(scored):
    assert scored.precision >= 0.9, scored.report()
    assert scored.recall >= 0.9, scored.report()


def test_every_claim_is_in_the_words_it_cites(scored):
    assert scored.grounding >= 0.8
    assert scored.third_person == 1.0


def test_a_question_stores_nothing():
    out = rules.extract(["what did I say about the Kestrel deadline?"], [1], 0.0)
    checked = validate.clean(out.as_dict(),
                             sources={1: "what did I say about the Kestrel deadline?"})
    assert checked.facts == []


def test_a_pronoun_object_is_resolved_to_what_it_refers_to():
    """"I'm allergic to it" keyed `user/allergic_x` and stored a claim naming
    no allergen: unretrievable, and uncorrectable."""
    facts = rules.find_facts(
        "penicillin is an antibiotic, that's not the point — I'm allergic to it")
    assert any("penicillin" in f["text"] for f in facts)
    assert not any(f["key"].endswith("_x") for f in facts)


def test_a_state_claim_keeps_its_verb():
    """Without the verb, "the car insurance on the third of March" is a
    subject, a date, and no claim."""
    facts = rules.find_facts(
        "remember that the car insurance renews on the third of March")
    assert any("renews" in f["text"] for f in facts)


def test_somebody_elses_preference_is_filed_under_them():
    facts = rules.find_facts(
        "Anderson said he prefers everything in writing, no phone calls")
    assert any(f["key"].startswith("anderson/") for f in facts)
    assert not any(f["key"].startswith("user/pref") for f in facts)


def test_a_name_does_not_keep_its_trailing_filler():
    facts = rules.find_facts("my name is Soumil by the way")
    name = next(f for f in facts if f["key"] == "user/name")
    assert name["text"].endswith("Soumil"), name["text"]


def test_a_capitalised_word_is_not_automatically_an_organisation():
    found = {e["name"]: e["kind"] for e in
             rules.find_entities("my sister Priya is moving to Lisbon")}
    assert found.get("Priya") == "person"
    assert found.get("Lisbon") == "place"


def test_a_clause_boundary_stops_a_pattern_running_into_the_next_claim():
    facts = rules.find_facts(
        "I don't work at Fenwick any more, I'm at Lumen now")
    employer = next(f for f in facts if f["key"] == "user/employer")
    assert "lumen" in employer["text"].lower()
    assert "fenwick" not in employer["text"].lower()


# ------------------------------------------------------------- self-correction

def test_a_correction_replaces_the_day_it_corrects():
    """Booking the reminder on the day they explicitly took back is worse
    than having no deadline at all: it reads as not listening."""
    out = commitments(
        "I'll call Anderson on Tuesday — no wait, Wednesday, he's away Tuesday",
        0.0)
    assert len(out) == 1
    assert "Wednesday" in out[0].text
    assert "Tuesday" not in out[0].text


def test_a_commitment_is_the_clause_not_the_paragraph():
    out = commitments(
        "actually I've switched to oat milk, and I'll book the boiler service "
        "on Tuesday", 0.0)
    assert len(out) == 1
    assert "milk" not in out[0].text


def test_a_deadline_with_no_correction_is_unchanged():
    out = commitments("I'll send him the revised numbers by Friday", 0.0)
    assert out[0].text == "I'll send him the revised numbers by Friday"
    assert out[0].expected_by is not None


# ------------------------------------------------------------------ the checker

def test_the_validator_earns_its_place(sloppy_raw, sloppy_clean):
    """The project's own standard: a mechanism whose removal changes nothing
    should be deleted. Against a model that is wrong the way real models are
    wrong, this is what it buys."""
    assert sloppy_raw.trap_hits and not sloppy_clean.trap_hits
    assert sloppy_clean.precision > sloppy_raw.precision + 0.2
    assert sloppy_clean.stability > sloppy_raw.stability
    assert sloppy_clean.recall >= sloppy_raw.recall - 0.05, \
        "cleaning should not cost recall"


def test_without_it_every_dimension_gets_a_fresh_key(sloppy_raw):
    """Which means supersession never fires and the store keeps every
    version of every preference forever."""
    assert sloppy_raw.stability == 0.0


def test_an_invented_fact_cannot_be_stored():
    source = {1: "the boiler service is due in October"}
    payload = {"facts": [
        {"key": "user/hobby", "text": "the user plays squash on Thursdays",
         "cites": [1], "confidence": 0.9},
        {"key": "boiler/service", "text": "the boiler service is due in October",
         "cites": [1], "confidence": 0.9},
    ]}
    kept = validate.clean(payload, sources=source)
    assert [f["key"] for f in kept.facts] == ["boiler/service"]
    assert "not in the source" in kept.rejected[0].reason


def test_a_quote_narrows_the_evidence_rather_than_widening_it():
    """A verbatim span proves the model read something real. It proves
    nothing about whether the claim follows, so quoting makes the check
    stricter: the claim is graded against the quote, not the batch."""
    source = {1: "no coriander in anything, ever",
              2: "I play squash on Thursdays"}
    honest = {"key": "user/coriander", "cites": [1, 2],
              "text": "the user will not eat coriander",
              "quote": "no coriander in anything"}
    # Every word of this is somewhere in the batch, so coverage alone passes
    # it. The quote it claims to have read does not support it.
    unsupported = {"key": "user/sport", "cites": [1, 2],
                   "text": "the user plays squash on Thursdays",
                   "quote": "no coriander in anything"}
    fabricated = {"key": "user/sport", "cites": [1],
                  "text": "the user plays squash on Thursdays",
                  "quote": "I play squash every single Thursday without fail"}

    assert validate.clean({"facts": [honest]}, sources=source).facts
    assert not validate.clean({"facts": [unsupported]}, sources=source).facts
    assert not validate.clean({"facts": [fabricated]}, sources=source).facts


def test_first_person_becomes_third():
    out = validate.to_third_person("I've switched to oat milk")
    assert out == "the user has switched to oat milk"
    assert validate.to_third_person("my sister is in Lisbon").startswith(
        "the user's sister")


def test_reported_speech_is_unwrapped_not_stored_as_metadata():
    out = validate.unwrap_reported("the user said that they prefer oat milk")
    assert "said" not in out
    assert "prefer" in out


@pytest.mark.parametrize("text,reason", [
    ("the user is running late this morning", "true today"),
    ("the user is shattered", "passing state"),
    ("the user might take the Lisbon job", "hypothetical"),
    ("the user asked about the Kestrel deadline", "conversation"),
    ("what did the user say about the deadline?", "question"),
])
def test_what_is_not_a_memory(text, reason):
    why = validate.undurable(text)
    assert why is not None, f"{text!r} should have been refused ({reason})"


def test_a_recurring_state_survives_the_transient_filter():
    """"Drained again after every Tuesday meeting" is the most valuable thing
    in the corpus and shares every word with the thing being filtered out."""
    assert validate.undurable(
        "the user is drained after every Tuesday planning meeting") is None


# ------------------------------------------------------------------------ keys

@pytest.mark.parametrize("raw,expected", [
    ("User/CoffeePreference", "user/coffee_preference"),
    ("user / coffee preference", "user/coffee_preference"),
    ("coffee_preference", "user/coffee_preference"),
    ("the user's milk preference", "user/milk_preference"),
    ("USER/MILK", "user/milk"),
])
def test_a_key_is_repaired_rather_than_guessed_at(raw, expected):
    assert validate.normalise_key(raw, "the user prefers oat milk") == expected


def test_a_value_laden_key_snaps_onto_the_dimension_already_in_use():
    """`user/pref_oat_milk` beside `user/pref_milk` is two rows for one
    dimension, and neither can ever replace the other."""
    key, snap = validate.canonical_key(
        "user/prefers_oat_milk", "the user drinks oat milk",
        "I've switched to oat milk", ["user/pref_milk"])
    assert key == "user/pref_milk"
    assert snap is not None


def test_a_proper_noun_is_never_the_dimension():
    """The guard that makes key-snapping safe. A common noun is what the
    claim is *about*; a name is its *value*. Without the distinction, "works
    at Fenwick" and "is at Lumen now" become two dimensions rather than two
    values of one, which is the exact error snapping exists to prevent."""
    assert validate.head_noun("the user has switched to oat milk",
                              "I've switched to oat milk") == "milk"
    assert validate.head_noun("the user works at Lumen",
                              "I'm at Lumen now") is None


def test_the_user_is_not_an_entity_in_their_own_graph():
    """A node connected to every episode is connected to nothing."""
    payload = {
        "facts": [{"key": "user/name", "text": "the user's name is Soumil",
                   "cites": [1]}],
        "entities": [{"name": "Soumil", "kind": "person", "cites": [1]},
                     {"name": "Fenwick", "kind": "org", "cites": [1]}],
    }
    kept = validate.clean(payload, sources={1: "my name is Soumil, I'm at Fenwick"})
    assert [e["name"] for e in kept.entities] == ["Fenwick"]


def test_a_short_name_folds_into_the_long_one():
    payload = {"entities": [
        {"name": "David Anderson", "kind": "person", "cites": [1]},
        {"name": "Anderson", "kind": "thing", "cites": [2]},
    ]}
    kept = validate.clean(payload, sources={1: "David Anderson", 2: "Anderson"})
    assert len(kept.entities) == 1
    assert "anderson" in kept.entities[0]["aliases"]


def test_a_complaint_is_not_a_commitment():
    payload = {"loops": [{"text": "someone should really fix that gate"},
                         {"text": "I need to book the boiler service"}]}
    kept = validate.clean(payload, sources={1: "x"})
    assert len(kept.loops) == 1
    assert "boiler" in kept.loops[0]["text"]


# ---------------------------------------------------------------- corrections

@pytest.fixture
def one_fact(tmp_path):
    """A store holding one belief, and a workspace that just recalled it."""
    from you2 import BrainConfig, SimClock
    from you2.activation.workspace import Item, Workspace
    from you2.substrate import HashEmbedder, Kind, Neuron, Store

    cfg = BrainConfig(db_path=str(tmp_path / "b.db"))
    store = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
    fact = store.add(Neuron(kind=Kind.FACT, key="user/pref_milk",
                            content="the user prefers dairy milk"))
    ws = Workspace()
    ws.items.append(Item(fact, 1.0, 1.0, 8, fact.text()))
    yield store, ws, fact
    store.close()


def _correct(one_fact, said):
    from you2.llm import FakeLLM
    from you2.recall import reconsolidate
    from you2.substrate import Kind
    store, ws, _fact = one_fact
    reconsolidate.apply(store, FakeLLM(), ws, said, store.cfg, store.clock.now())
    return [n.text() for n in store.by_kind(Kind.FACT, limit=10) if n.is_live]


@pytest.mark.parametrize("said,expected", [
    ("no, I drink semi-skimmed now", "the user drinks semi-skimmed now"),
    ("actually I prefer oat milk now", "the user prefers oat milk now"),
    ("no, that's out of date, I'm at Lumen now", "the user is at Lumen now"),
])
def test_a_correction_is_stored_as_a_claim_about_the_person(one_fact, said,
                                                            expected):
    """Third person, and the marker stripped rather than the clause dropped.
    This path wrote straight to the store without the checks every other
    writer goes through, so "I drink semi-skimmed now" went in verbatim —
    first person, under the milk key, ready to be read back aloud."""
    assert _correct(one_fact, said) == [expected]


@pytest.mark.parametrize("said", ["no, that is not right", "no, not any more"])
def test_a_bare_retraction_stores_nothing_at_all(one_fact, said):
    """"No, not any more" is a complete instruction and it means exactly what
    `invalid_at` already encodes. It used to store a fact reading "the user
    says this is no longer true: <the old claim>" — the tombstone written as
    content, holding the dimension's key, so the next real statement
    superseded the tombstone instead of the belief."""
    assert _correct(one_fact, said) == []


def test_the_replacement_is_the_last_clause_not_the_longest():
    """A correction says what is wrong and then what is right, in that order.
    Longest picked "that's out of date" over "I'm at Lumen now"."""
    from you2.recall.reconsolidate import _restate
    assert _restate("no, that's out of date, I'm at Lumen now") == "I'm at Lumen now"

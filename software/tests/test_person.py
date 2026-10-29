"""The person: who they are, not just what they said.

The failure mode being tested against throughout is confidently telling
someone something about themselves that is wrong. That is far worse than
saying nothing — it is the moment they stop believing any of it — so most of
these assert that thin evidence produces *silence*.
"""

from __future__ import annotations

import pytest

from you2 import DAY, BrainConfig, SimClock
from you2.clock import weekday
from you2.person import build, rhythms, stance, style
from you2.substrate import Edge, HashEmbedder, Kind, Neuron, Store


@pytest.fixture
def store(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "brain.db"))
    s = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
    yield s
    s.close()


def episode(store, text, *, at, valence=0.0, arousal=0.5, entity=None):
    n = store.add(Neuron(kind=Kind.EPISODE, content=text, created_at=at,
                         last_access=at, valence=valence, arousal=arousal,
                         salience=0.5))
    if entity is not None:
        store.connect(n.id, entity, Edge.ABOUT, 0.9, at=at)
    return n


def tuesdays(start, n):
    """Actual Tuesdays, from the calendar — not `day % 7`, which is how the
    eval harness once planted a year of 'Tuesday meetings' on Fridays."""
    out, day = [], 0
    while len(out) < n:
        t = start + day * DAY + 18 * 3600
        if weekday(t) == "tuesday":
            out.append(t)
        day += 1
    return out


# ----------------------------------------------------------------- rhythms

def test_rhythms_say_nothing_without_enough_history(store):
    now = store.clock.now()
    for i in range(6):
        episode(store, f"a thing {i}", at=now - i * DAY, valence=-0.6)
    r = rhythms.build(store, store.cfg, now)
    assert not r.reliable
    assert r.brief() == [], "six episodes is not a rhythm"


def test_a_real_weekly_pattern_is_found(store):
    """The planted signal: consistently flat on Tuesday evenings, against a
    neutral background the rest of the week."""
    start = store.clock.now()
    for t in tuesdays(start, 14):
        episode(store, "the planning meeting drained me", at=t, valence=-0.8)
    day = 0
    while day < 100:
        t = start + day * DAY + 11 * 3600
        if weekday(t) != "tuesday":
            episode(store, f"an ordinary day {day}", at=t, valence=0.05)
        day += 1

    now = start + 101 * DAY
    r = rhythms.build(store, store.cfg, now)
    assert r.reliable
    found = {label for label, _what, _z in r.notable()}
    assert "tuesday" in found
    assert any("tuesday" in line for line in r.brief())


def test_a_bucket_below_the_sample_floor_is_never_reported(store):
    start = store.clock.now()
    for t in tuesdays(start, 3):                      # three Tuesdays only
        episode(store, "awful", at=t, valence=-0.9)
    for day in range(90):
        t = start + day * DAY + 11 * 3600
        if weekday(t) != "tuesday":
            episode(store, "fine", at=t, valence=0.1)
    r = rhythms.build(store, store.cfg, start + 91 * DAY)
    assert all(label != "tuesday" for label, _w, _z in r.notable())


# ------------------------------------------------------------------ stance

def test_stance_tracks_the_direction_not_just_the_level(store):
    """Most people are mildly negative about most work; that carries no
    information. 'More negative than two months ago' carries a great deal."""
    now = store.clock.now()
    ent = store.add(Neuron(kind=Kind.ENTITY, content="the Kestrel migration"))
    for i in range(12):
        at = now + i * 7 * DAY
        episode(store, f"kestrel update {i}", at=at,
                valence=0.4 - 0.09 * i, entity=ent.id)

    stances = stance.build(store, store.cfg, now + 90 * DAY)
    s = stances[ent.id]
    assert s.reliable
    assert s.trend < 0
    assert s.drifting == "souring"
    assert "souring" in s.describe(now + 90 * DAY)


def test_a_stable_stance_is_not_reported_as_drifting(store):
    now = store.clock.now()
    ent = store.add(Neuron(kind=Kind.ENTITY, content="the commute"))
    for i in range(12):
        episode(store, f"commute {i}", at=now + i * 5 * DAY,
                valence=-0.1, entity=ent.id)
    s = stance.build(store, store.cfg, now + 70 * DAY)[ent.id]
    assert s.drifting is None


def test_too_few_mentions_yields_no_trend(store):
    now = store.clock.now()
    ent = store.add(Neuron(kind=Kind.ENTITY, content="a passing thing"))
    for i in range(3):
        episode(store, f"x {i}", at=now + i * DAY, valence=-0.9, entity=ent.id)
    stances = stance.build(store, store.cfg, now + 10 * DAY)
    assert not stances[ent.id].reliable
    assert stance.notable(stances, now + 10 * DAY) == []


def test_stale_stances_are_not_surfaced(store):
    now = store.clock.now()
    ent = store.add(Neuron(kind=Kind.ENTITY, content="an old project"))
    for i in range(10):
        episode(store, f"old {i}", at=now + i * DAY, valence=-0.7, entity=ent.id)
    later = now + 300 * DAY
    assert stance.notable(stance.build(store, store.cfg, later), later) == []


# ------------------------------------------------------------------- style

def test_style_says_nothing_from_a_handful_of_turns(store):
    now = store.clock.now()
    for i in range(5):
        episode(store, "a short line", at=now - i * DAY)
    s = style.build(store, store.cfg, now)
    assert not s.reliable
    assert s.instruction() == ""


def test_a_terse_speaker_is_answered_tersely(store):
    now = store.clock.now()
    for i in range(40):
        episode(store, "wall done yet", at=now - i * 3600)
    s = style.build(store, store.cfg, now)
    assert s.reliable and s.terse
    assert "short sentences" in s.instruction()


def test_imported_documents_do_not_teach_it_how_you_talk(store):
    """Otherwise one imported vault convinces the device you speak in
    specifications."""
    now = store.clock.now()
    for i in range(40):
        n = store.add(Neuron(kind=Kind.EPISODE, created_at=now - i * 3600,
                             last_access=now,
                             content="This section describes the procedure by "
                                     "which the aforementioned parameters shall "
                                     "be determined and subsequently applied."))
        n.source_ref = "document"
        store.save(n)
    for i in range(30):
        episode(store, "yeah sounds good", at=now - i * 60)
    s = style.build(store, store.cfg, now)
    assert s.terse, "document text leaked into the style model"


# ------------------------------------------------------------------ person

def test_the_persona_is_empty_on_a_fresh_device(store):
    person = build(store, store.cfg, store.clock.now())
    assert person.persona().strip() == ""
    assert "0 identity" in person.explain() or "identity      0" in person.explain()


def test_the_persona_carries_identity_preferences_and_concerns(store):
    now = store.clock.now()
    store.add(Neuron(kind=Kind.FACT, key="user/name",
                     content="the user's name is Soumil", confidence=0.95))
    store.add(Neuron(kind=Kind.FACT, key="user/pref_milk",
                     content="the user prefers oat milk", confidence=0.9))
    store.add(Neuron(kind=Kind.LOOP, content="book the boiler service",
                     created_at=now - 40 * DAY, last_access=now,
                     meta={"expected_by": now - 20 * DAY}))

    person = build(store, store.cfg, now)
    block = person.persona(now)
    assert "Soumil" in block
    assert "oat milk" in block
    assert "boiler" in block and "overdue" in block


def test_the_arc_reads_supersession_for_free(store):
    """A fact that replaced another *is* a dated change in the person, and
    nothing else has to do any work for this to exist."""
    now = store.clock.now()
    old = store.add(Neuron(kind=Kind.FACT, key="user/employer",
                           content="the user works at Fenwick", created_at=now))
    store.clock.advance(days=30)
    new = store.add(Neuron(kind=Kind.FACT, key="user/employer",
                           content="the user works at Lumen",
                           created_at=store.clock.now()))
    old.superseded_by = new.id
    old.invalid_at = store.clock.now()
    store.save(old)

    person = build(store, store.cfg, store.clock.now())
    assert any("Fenwick" in line and "Lumen" in line for line in person.arc)


def test_every_claim_can_show_its_evidence(store):
    """A model of a person that cannot show its evidence is one they have no
    way to correct."""
    now = store.clock.now()
    ent = store.add(Neuron(kind=Kind.ENTITY, content="the Kestrel migration"))
    for i in range(12):
        episode(store, f"kestrel {i}", at=now + i * 7 * DAY,
                valence=0.4 - 0.09 * i, entity=ent.id)
    person = build(store, store.cfg, now + 90 * DAY)
    text = person.explain()
    assert "mentions" in text
    assert "Kestrel" in text
    assert "well-evidenced" in text


def test_the_snapshot_round_trips(store):
    from you2.person import load_snapshot, save
    person = build(store, store.cfg, store.clock.now())
    save(store, person)
    assert load_snapshot(store) is not None


def test_a_rhythm_is_gated_on_certainty_not_on_size():
    """The old gate divided by the per-episode standard deviation, which does
    not shrink as evidence accumulates, and compared to a fixed constant. So
    it was silent on a planted weekly rhythm measured over a year at 11
    standard errors, and fired on a bucket sitting on the n=8 floor."""
    from you2.person.rhythms import Bucket, Rhythms

    r = Rhythms(baseline_valence=-0.0582, spread_valence=0.2378,
                total=1151, span_days=365.0)
    r.by_weekday["tuesday"] = Bucket("tuesday", n=225, valence=-0.2356)
    labels = [label for label, _what, _t in r.notable()]
    assert "tuesday" in labels, r.notable()

    # A difference too small to matter stays quiet however many observations
    # produced it.
    quiet = Rhythms(baseline_valence=0.0, spread_valence=0.2378,
                    total=5000, span_days=365.0)
    quiet.by_weekday["friday"] = Bucket("friday", n=4000, valence=0.004)
    assert quiet.notable() == []


def test_best_hours_says_nothing_when_the_parts_are_a_coin_flip():
    """A bare argmax over three buckets always has a winner. On a real year
    afternoon beat morning by 0.0035 valence against a standard error of
    0.012 -- and this is the only rhythm line that reaches the prompt."""
    from you2.person.rhythms import Bucket, Rhythms

    r = Rhythms(baseline_valence=-0.058, spread_valence=0.2378,
                total=1151, span_days=365.0)
    r.by_part["evening"] = Bucket("evening", n=419, valence=-0.1432, arousal=0.5359)
    r.by_part["afternoon"] = Bucket("afternoon", n=387, valence=-0.0078, arousal=0.5313)
    r.by_part["morning"] = Bucket("morning", n=345, valence=-0.0116, arousal=0.5301)
    assert r.best_hours() is None

    r.by_part["afternoon"] = Bucket("afternoon", n=387, valence=0.35, arousal=0.53)
    assert r.best_hours() == "afternoon"


def test_souring_is_still_visible_after_two_years():
    """_slope fitted one line over the entity's entire history. Valence is
    bounded, so the largest slope a span of T days can produce is 60/T per
    month against a fixed DRIFT of 0.18 -- making the gate unreachable past
    ~333 days, however hard someone sours."""
    from you2.clock import DAY
    from you2.person.stance import DRIFT, _slope

    for span in (90, 180, 365, 730):
        now = span * DAY
        points = [(d * DAY, -0.8 if d > span - 30 else 0.0) for d in range(span)]
        trend = _slope(points, now)
        assert abs(trend) >= DRIFT, f"{span}d span: trend {trend}"

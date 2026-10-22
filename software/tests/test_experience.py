"""The face: the event bus, the cascade scene, the server, the light bar.

None of this is load-bearing — strip every subscriber and the brain behaves
identically. These tests exist to keep it that way, and to keep the one thing
that *is* product-critical honest: the visualisation must show what actually
happened, not a plausible animation of it.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from you2 import BrainConfig, SimClock
from you2.activation import recall
from you2.llm import FakeLLM
from you2.runtime import Bar, Brain, Bus, State, serve, viz
from you2.substrate import Edge, HashEmbedder, Kind, Neuron


@pytest.fixture
def brain(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "brain.db"))
    b = Brain(cfg, clock=SimClock(), llm=FakeLLM(),
              embedder=HashEmbedder(dim=cfg.embed_dim), bus=Bus())
    yield b
    b.close_store()


def seed(brain):
    for text in ["I had lunch with David Anderson about the Kestrel contract",
                 "I will send him the revised numbers by Friday",
                 "I really cannot stand cilantro",
                 "the garden wall needs rebuilding before winter"]:
        brain.say(text)
        brain.clock.advance(seconds=400)
    brain.close()


# ------------------------------------------------------------------ the bus

def test_publishing_never_blocks_when_nobody_is_reading():
    """A slow display must not cost a turn."""
    bus = Bus()
    q = bus.subscribe()
    for i in range(Bus.DEPTH * 4):
        bus.publish("cascade", i=i)
    assert q.qsize() <= Bus.DEPTH
    # Newest wins: a stale animation frame is worthless.
    newest = None
    while not q.empty():
        newest = q.get_nowait()
    assert newest.data["i"] == Bus.DEPTH * 4 - 1


def test_a_late_subscriber_gets_the_current_state():
    """A browser refresh must not leave the eye blank."""
    bus = Bus()
    bus.state_changed(State.DREAMING)
    bus.publish("cascade", nodes=[])
    q = bus.subscribe()
    kinds = {q.get_nowait().type for _ in range(q.qsize())}
    assert "state" in kinds and "cascade" in kinds


def test_events_serialise_as_sse():
    bus = Bus()
    raw = bus.publish("state", state="idle").sse()
    assert raw.startswith(b"event: state\n")
    assert b"\n\n" in raw
    payload = json.loads(raw.split(b"data: ")[1])
    assert payload["state"] == "idle"


def test_the_brain_is_identical_with_no_subscribers(tmp_path):
    """The visualisation reads state; it never creates it."""
    def run(bus):
        cfg = BrainConfig(db_path=":memory:")
        b = Brain(cfg, clock=SimClock(), llm=FakeLLM(),
                  embedder=HashEmbedder(dim=cfg.embed_dim), bus=bus)
        for t in ["I really cannot stand cilantro", "I prefer oat milk"]:
            b.say(t)
            b.clock.advance(seconds=400)
        b.close()
        out = (b.store.count(), b.store.graph.edge_count,
               [n.content for n in b.store.by_kind(Kind.FACT, limit=20)])
        b.close_store()
        return out

    quiet = Bus()
    watched = Bus()
    watched.subscribe()
    assert run(quiet) == run(watched)


# ---------------------------------------------------------------- the scene

def test_the_scene_is_the_real_cascade(brain):
    seed(brain)
    ws = recall(brain.store, "how is the Kestrel contract going with Anderson",
                infer=True)
    scene = viz.scene(brain.store, ws, query="anderson")

    assert scene.visited == ws.cascade.visited
    assert scene.workspace == ws.ids()
    assert scene.tokens == ws.tokens
    ids = {n["id"] for n in scene.nodes}
    # Every winner must be drawn, or the animation shows an answer arriving
    # from nowhere.
    assert set(ws.ids()) <= ids
    # Every edge must join two drawn nodes.
    for e in scene.edges:
        assert e["from"] in ids and e["to"] in ids
    # The firing order is the real one.
    assert scene.order == [n for n in ws.cascade.fired if n in ids]


def test_the_scene_records_what_inhibition_suppressed(brain):
    hub = brain.store.add(Neuron(kind=Kind.ENTITY, content="user")).id
    dupes = [brain.store.add(Neuron(kind=Kind.FACT, key=f"d{i}",
                                    content="prefers oat milk in coffee"))
             for i in range(5)]
    for d in dupes:
        brain.store.connect(hub, d.id, Edge.ASSOC, 0.9)
    from you2.activation import Cue, compete, spread
    cas = spread(brain.store.graph, [Cue(hub, 1.0)], brain.cfg)
    ws = compete(brain.store, cas, brain.cfg)
    scene = viz.scene(brain.store, ws)
    assert any(n["suppressed"] for n in scene.nodes), \
        "lateral inhibition has to be visible, or the picture lies about it"


def test_scene_is_bounded(brain):
    for i in range(400):
        brain.store.add(Neuron(kind=Kind.EPISODE, content=f"thing {i} about walls"))
    ws = recall(brain.store, "walls")
    scene = viz.scene(brain.store, ws)
    assert len(scene.nodes) <= viz.MAX_NODES + len(ws.ids())
    assert len(scene.edges) <= viz.MAX_EDGES


def test_constellation_is_a_portrait_not_a_retrieval(brain):
    seed(brain)
    c = viz.constellation(brain.store)
    assert c["neurons"] == brain.store.stats()["neurons"]
    assert c["nodes"]
    ids = {n["id"] for n in c["nodes"]}
    for e in c["edges"]:
        assert e["from"] in ids and e["to"] in ids


# --------------------------------------------------------------- the server

@pytest.fixture
def server(brain):
    seed(brain)
    httpd = serve(brain, host="127.0.0.1", port=0, bus=brain.bus, background=True)
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url, path):
    with urllib.request.urlopen(url + path, timeout=5) as r:
        return json.loads(r.read())


def post(url, path, payload):
    req = urllib.request.Request(
        url + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def test_the_eye_is_served(server):
    with urllib.request.urlopen(server + "/", timeout=5) as r:
        body = r.read().decode()
    assert "<canvas" in body and 'id="eye"' in body


def test_api_round_trip(server):
    assert get(server, "/api/state")["state"]
    assert get(server, "/api/constellation")["nodes"]
    reply = post(server, "/api/say", {"text": "how is Anderson"})
    assert reply["text"]
    assert reply["route"] in ("model", "memory", "router")


def test_saying_something_publishes_a_cascade(server, brain):
    q = brain.bus.subscribe()
    post(server, "/api/say", {"text": "how is the Kestrel contract"})
    kinds = []
    while not q.empty():
        kinds.append(q.get_nowait().type)
    assert "cascade" in kinds
    assert "state" in kinds


def test_path_traversal_is_refused(server):
    with pytest.raises(Exception):
        urllib.request.urlopen(server + "/../you2/config.py", timeout=5)


# ------------------------------------------------------------- the light bar

def test_the_bar_is_a_no_op_without_a_device(tmp_path):
    bar = Bar(device=tmp_path / "nope")
    assert not bar.live
    bar.write([(255, 255, 255)] * 16)     # must not raise
    bar.stop()


def test_brightness_is_clamped():
    """WS2812 draws ~18 mA per channel, not per LED. Sixteen pixels at full
    white is ~0.9 A; the separate 5 V rail is sized for the clamped figure."""
    from you2.runtime.leds import MAX
    bar = Bar()
    ceiling = int(255 * MAX) + 1
    for state in State:
        for t in (0.0, 0.7, 1.7, 3.3):
            for channel in bar.frame(state, t):
                assert max(channel) <= ceiling, (state, channel)


def test_the_bar_follows_the_level_published_by_the_audio_loop():
    """The bar polled bus.state and nothing else, so set_level was never called
    and _level stayed 0.0 for the life of the process. LISTENING and SPEAKING
    both render mode "level" — the two states anyone actually watches were
    drawing a meter pinned at zero, while the loop published a level event 33
    times a second that had no consumer.
    """
    import time as _time
    from pathlib import Path
    from you2.runtime.events import Bus

    bus = Bus()
    bar = Bar(device=Path("/nonexistent"), fps=120.0)
    # At level 0 the meter is a dim floor with a faint centre; the point is
    # that it never moved off it, whatever was said.
    quiet = sum(sum(px) for px in bar.frame(State.LISTENING, 0.0))

    bar.start(bus)
    try:
        bus.publish("level", level=1.0)
        loud = quiet
        for _ in range(200):                    # ~1 s ceiling, not a fixed sleep
            loud = sum(sum(px) for px in bar.frame(State.LISTENING, 0.0))
            if loud != quiet:
                break
            _time.sleep(0.005)
    finally:
        bar.stop()
    assert loud > quiet, ("the bar never saw the level event — this is the bug: "
                          f"full scale rendered {loud}, silence renders {quiet}")


def test_each_state_looks_different():
    bar = Bar()
    seen = {state: bar.frame(state, 2.0) for state in State}
    assert len({tuple(v) for v in seen.values()}) >= len(State) - 1

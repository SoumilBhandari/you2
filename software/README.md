# The brain

The cold path from [docs/design.md](../docs/design.md), built. Design and
reasoning: **[docs/brain.md](../docs/brain.md)** — read that first.

```bash
cd software
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

.venv/bin/you2 demo                # seed a month of life and open the eye
.venv/bin/you2 listen              # the whole device: ears, brain, voice
.venv/bin/you2 chat --fake         # talk to it, no network, no model download
.venv/bin/you2 why "how's Anderson" # print the whole retrieval
.venv/bin/you2 night                # run consolidation now
.venv/bin/you2 eval --days 365      # simulate a year, measure the curve
.venv/bin/you2 eval --ablate        # check every mechanism earns its place
.venv/bin/pytest                    # 194 tests, offline, deterministic
```

Nothing here needs hardware, a network, or a model download. `numpy` is the
only hard dependency.

---

## What it is

Not a vector store with a cron job. Four commitments, each with a mechanism:

**Retrieval is bounded spreading activation.** Embeddings pick a handful of
starting points; everything after that is graph traversal with a decay term
and a firing threshold. Retrieval cost is set by the shape of the cascade, not
by the size of the store — a test asserts that a 30× larger store visits the
same number of neurons for the same cue. The system *structurally cannot* read
everything.

**The token budget is a global workspace.** Selection is a competition with
lateral inhibition, not a top-k truncation. Winning has consequences: winners
are strengthened and wired to each other, which is where the association graph
actually comes from.

**Salience is prediction error.** The workspace is what memory predicted would
be relevant, so surprise is the distance from the retrieval that already ran.
One dot product, no model call, no asking a model to rate importance.

**Nightly multiplicative downscaling** is what makes retrieval sharpen with
age instead of degrading. Over 90 simulated nights on a graph of one reinforced
cluster plus 900 unreinforced noise edges, activation landing inside the
cluster goes 0.785 → 1.000 and the graph goes 832 edges → 45.

And, because recall alone is a filing cabinet:

**The weights are the cortex; the graph is the person.** World knowledge lives
in the model and is never stored. Inference is lazy, so nothing is
materialised by default — except a derivation drawn three times, which
compiles into a stored fact carrying its premises. It is the only thing here
that grows with *use* rather than with elapsed time.

---

## The person

Memory answers *what did they say*. This answers *who are they*, and it is the
difference between a good search index and something that is actually theirs.

Six components, every one derived from what is already in the store and none of
them a setting anyone picked:

| | |
|---|---|
| **identity** | the durable self |
| **rhythms** | the shape of a week — when they are sharp, when they are flat |
| **stances** | how they feel about each thing, and *which way it is moving* |
| **style** | how they talk, and therefore how to talk back |
| **concerns** | what is live and weighing on them |
| **arc** | what has changed about them lately |

On a seeded month it produces things only something that has watched you for
weeks can say: *souring on Kestrel contract* (11 mentions over 5 weeks, negative
trend), *at their best in the morning*, *boiler service — overdue*, *they speak
in short sentences — match that*.

The failure mode designed against throughout is confidently telling someone
something wrong about themselves. That is far worse than saying nothing — it is
the moment they stop believing any of it. So every claim carries its sample
size, thin evidence produces an empty section rather than a hedged one, and
`explain()` prints what each line was derived from. Most of the tests assert
*silence*.

---

## Ears

The wake word is **"Hey Friday"**. Five stages, each a Protocol with a real
adapter and a working fallback, so the whole loop runs and is tested with no
models and no hardware:

| stage | device | fallback |
|---|---|---|
| wake | openWakeWord, custom model | transcript-driven |
| VAD | Silero v6 | adaptive-energy, minimum statistics |
| endpointing | pipecat smart-turn v3 | syntax-aware silence |
| ASR | Moonshine streaming | scripted, with real partials |
| TTS | Piper | speech-shaped noise, correctly timed |

```bash
you2 listen --mic          # the whole device
you2 listen                # same loop, typed input, correctly-shaped audio
```

Barge-in is the item [design.md](../docs/design.md) ranks as most likely to
sink the project and says cannot be tested without hardware. That is true of the
*acoustic* half — whether AEC holds with a speaker 60 mm from a microphone. It
is not true of the orchestration half, which is whether capture keeps running
while it talks and cancels when it hears you. That half is most of the code and
it is tested.

---

## Layout

```
you2/
  config.py        every tunable, one dataclass
  clock.py         injectable time — architecture, not a test helper
  substrate/       neurons, synapses, CSR graph, two-tier vectors, SQLite
  activation/      base-level decay, spreading, inhibition, workspace
  perception/      prosody, attention state machine, discourse, salience
  encoding/        segmentation, binding — no model call, works offline
  consolidation/   night orchestrator, replay, abstraction, supersession,
                   homeostasis, forgetting, dream, storage accounting
  discovery/       contradiction, recurrence, open loops, bridging, scoring
  inference/       engine, compilation, truth maintenance, parsimony
  person/          identity, rhythms, stance, style — who they are
  audio/           wake word, VAD, endpointing, ASR, TTS, the hot loop
  recall/          retrieve, memory-answerable answers, reconsolidation
  llm/             OpenAI-compatible client, rule extractor, deterministic fake
  runtime/         hot path, intent router, event bus, eye, light bar, CLI
  eval/            synthetic history with ground truth, harness, ablations
ui/                the eye: 800x800, masked to a circle, canvas 2D
```

Two escape hatches keep the whole thing runnable with no network and no
downloads: a deterministic hash embedder and a rule-based fake model. That is
why the test suite is offline, fast, and byte-identical everywhere.

---

## What the numbers say

One simulated year, 1,178 utterances, 353 nights, ~44 s wall clock:

| day | probe precision | stale leakage | neurons | live facts | edges |
|---|---|---|---|---|---|
| 30 | 0.50 | 0.00 | 118 | 8 | 762 |
| 90 | 0.50 | 0.00 | 297 | 8 | 1,333 |
| 180 | 0.50 | 0.00 | 556 | 9 | 1,495 |
| 365 | **1.00** | **0.00** | 1,075 | 13 | 2,150 |

Precision **rises** with elapsed time and superseded values never reach the
block. That is the one chart that matters: if it fell, consolidation would not
be working, and every memory system that has shipped fails exactly there.

**What this does and does not measure.** It exercises the consolidation
machinery — supersession, downscaling, decay, tiering, replay prioritisation,
discovery scoring, the whole write path — deterministically, with ground truth,
because the corpus was generated. Bridging reports as untested rather than as
working, because a rule-based judge genuinely cannot evaluate an analogy and
the fake declines rather than pretending.

What it cannot see is whether what went in was *right*, because it feeds the
rule extractor a corpus those same rules generated. That needs its own
instrument.

---

## Is what goes into the store correct

```bash
you2 extract                    # score against 24 hand-labelled sessions
you2 extract --report           # every miss, trap and unasked-for claim
you2 extract --live             # …and the real model, if you have a key
you2 extract "I'm at Lumen now" # what would this one line store?
```

24 sessions of speech as people actually produce it, labelled by hand with
what a careful reader should take from them and — the half that matters — 22
**traps** they must not take: questions, hypotheticals, world knowledge, other
people's opinions, and things that were true for an afternoon.

| | precision | recall | key stability | traps | grounding |
|---|---|---|---|---|---|
| rules | 1.00 | 1.00 | 0.67 | 0 | 0.89 |
| a sloppy model, raw | 0.66 | 1.00 | **0.00** | 4 | 0.64 |
| the same model, checked | 1.00 | 1.00 | **0.67** | 0 | 0.89 |

**Key stability** is the number that matters and the one nobody measures: the
same dimension, stated twice weeks apart in different words — one key, or two?
Two means supersession never fires and the store keeps every version of every
preference forever. Its opposite failure is measured alongside it, because
stability on its own is trivially gamed by mapping everything to one key.

So nothing an extractor produces is trusted, the rules included.
[`llm/validate.py`](you2/llm/validate.py) checks shape, person, durability,
grounding and keys, and [`eval/sloppy.py`](you2/eval/sloppy.py) is a model
that is wrong the way real models are wrong — values welded into keys,
first-person claims, invented facts, questions echoed back as claims — so that
what the checking buys is a measurement rather than an assertion.

The rules scoring 1.00 should be read with suspicion: the corpus is mine and I
fixed what it found. The rule held to is that a fix must be a whole
construction or it does not go in. One labelled failure is left standing —
"I only ever buy semi-skimmed", said after "we're out of milk", is about milk,
and no rule recovers a head noun that was never spoken.

---

## The instrument that matters most

There is no benchmark for "does this feel like it knows me after two years", so
the rating loop exists from the first day. Everything surfaced is logged with
its four axis scores and a slot for a one-keypress verdict; after three months
that is a precision figure **per connection type**, which tells you exactly
which categories to cut.

`you2 why "..."` prints the whole retrieval — cues, cascade, what won and by
what path, what lateral inhibition suppressed. A memory system you cannot
inspect is one you cannot tune, and this one will be tuned for months.

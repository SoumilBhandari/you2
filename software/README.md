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
.venv/bin/you2 extract              # is what goes INTO the store correct
.venv/bin/pytest                    # 286 tests, offline, deterministic
```

Nothing here needs hardware, a network, or a model download. `numpy` is the
only hard dependency. ~16k lines.

Picking this up cold? **[../handoff_software.md](../handoff_software.md)** —
current numbers, what changed and why, and what will waste your afternoon.

---

## What it is

Not a vector store with a cron job. Four commitments, each with a mechanism:

**Retrieval is bounded spreading activation.** Embeddings pick a handful of
starting points; everything after that is graph traversal with a decay term
and a firing threshold. Retrieval cost is set by the shape of the cascade, not
by the size of the store: a 10× larger store visits the same dozen neurons and
takes 0.52 ms against 0.40 ms. The system *structurally cannot* read
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

## It has to still be there tomorrow

The commitments above are about *quality* of memory. This one is about whether
there is any.

`sqlite3` opens an implicit deferred transaction on the first write, and for
most of this project's life `store.flush()` was the only commit in the system
— reached by `Brain.close()`, `store.close()` and the night, none of which a
turn touches. Under the shipped unit (`you2 serve`, `Restart=always`, and no
signal handler anywhere) every restart, reboot or OOM kill on a 2 GB Pi
discarded everything since the last consolidation. Measured:

| | neurons | episodes | edges |
|---|---|---|---|
| in process, after 60 turns | 61 | 59 | 181 |
| on disk, after `os._exit(9)` | **0** | **0** | **0** |

The turn now commits as the last thing it does, after the reply and after the
encode, for about 0.1 ms against a 2.3 ms turn. The order of that fix mattered:
`persist_graph` deletes every edge and re-inserts them inside one transaction
on the night thread, so a naive per-turn commit landing in that window would
have made the *empty* edge table durable and put it beyond rollback. `flush()`
is a no-op inside a transaction for exactly that reason.

**"Forgotten." is durable too**, and reaches further than the neuron: the
entity extracted from a deleted memory, its aliases, the batch gist shared
with its neighbours, the session summary, the journal, and the question that
retrieved it. An entity is carried by memories that are *about* it — counting
the ASSOC edges `broadcast` writes between co-activated winners meant that
merely *asking* about something before deleting it kept its name alive.

Both are verified by killing a subprocess, not by mocking: `close()`, `__del__`
and `atexit` would each hide the bug.


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
  llm/             OpenAI-compatible client, rule extractor, the validator
                   every extractor's output must pass, deterministic fake
  runtime/         hot path, intent router, event bus, eye, light bar, CLI
  eval/            synthetic history with ground truth, harness, ablations,
                   the hand-labelled extraction corpus, and a model that is
                   wrong the way real models are wrong
ui/                the eye: 800x800, masked to a circle, canvas 2D
```

Two escape hatches keep the whole thing runnable with no network and no
downloads: a deterministic hash embedder and a rule-based fake model. That is
why the test suite is offline, fast, and byte-identical everywhere.

---

## What the numbers say

Two simulated years, 2,325 utterances, 707 nights, ~133 s wall clock:

| day | probe precision | focus | stale leakage | neurons | live facts | edges |
|---|---|---|---|---|---|---|
| 30 | 0.50 | 0.86 | 0.00 | 117 | 5 | 671 |
| 90 | 0.50 | 0.88 | 0.00 | 294 | 5 | 1,320 |
| 365 | 0.50 | 0.73 | 0.00 | 1,065 | 7 | 1,915 |
| **730** | **1.00** | **0.91** | **0.00** | 2,077 | 11 | 2,743 |

Two years of daily use is **8.9 MB on disk**. The whole premise — put the
world knowledge in the weights and the store stays small — measured rather
than asserted.

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
| rules | 1.00 | 1.00 | 1.00 | 0 | 0.89 |
| a sloppy model, raw | 0.66 | 1.00 | **0.00** | 4 | 0.64 |
| the same model, checked | 1.00 | 1.00 | **1.00** | 0 | 0.89 |

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
construction or it does not go in.

The last one to fall is the one worth knowing about. "We've run out of milk —
I only ever buy semi-skimmed" leaves the head noun unsaid, and an extractor
that does not restore it keys the claim on `skimm`. Six weeks later "I've
switched to oat milk" keys on `milk`, the store sees two dimensions rather
than one, **supersession never fires**, and both preferences sit in the
profile forever contradicting each other. Rot arriving through a gap in the
grammar rather than a gap in the architecture.

---

## Every mechanism earns its place, or goes

`you2 eval --ablate` turns each one off and measures what breaks. Deltas at
one year:

| removing | costs |
|---|---|
| fan effect | focus 0.89 → 0.34, visited 14 → **380** |
| inhibition | diversity 0.91 → **0.33** |
| spreading (top-k only) | diversity 0.91 → 0.38, and the **highest** focus of any variant |
| base-level decay | stale leakage 0.00 → **0.50** |
| downscaling | edges 2,024 → 4,283 |
| the item cap (12 → 40) | tokens 132 → 251 |
| **the token budget (400 → 4000)** | **nothing at all** |
| **the salience gate** | **nothing at all** |

`top-k-only` scoring the best focus and nearly the worst diversity is naive
RAG's failure mode measured rather than asserted: it returns the most on-topic
set and the least informative one.

The last two rows are the point of running this. `docs/brain.md` §1.2 says the
400-token cap *is* the workspace capacity — it is inert, and the real capacity
is `workspace_max_items`. And the salience gate has now failed to move any
metric across many revisions, while being the proximate cause of four separate
defects, because the material it suppresses — repetition, closure,
confirmation, correction — is exactly what recurrence, loop closure,
supersession and reconsolidation each need.

---

## How fast, and how that scales

Full retrieval, with realistic ageing and one nightly tiering pass:

| store | cue selection | spreading | workspace | **full recall** |
|---|---|---|---|---|
| 10,000 neurons | 0.19 ms | 0.06 ms | 0.33 ms | **0.40 ms** |
| 100,000 neurons | 0.29 ms | 0.06 ms | 0.31 ms | **0.52 ms** |

Flat across a 10× range. It was not: full recall was 22.6 ms at 100k, and
98% of it was ambient priming issuing `kind = EPISODE ORDER BY created_at DESC
LIMIT 12` on **every turn** against separate indexes on `kind` and
`created_at`, so SQLite took one and sorted the rest. A composite index made
it a twelve-row scan.

It hid because the cascade was visiting its usual dozen neurons throughout —
a bounded traversal hanging off an unbounded lookup — and because the store
grows so slowly that no realistic corpus would ever have reached it. The case
that would have hit it in production is a bulk `you2 import` of a document
vault, presenting as "the device got slow" with nothing in the design to blame.

---

## How the bugs actually get found

Not by the test suite. It was green for every defect listed below.

**Run it and read the output.** Feeding the write path hostile input found a
102 KB neuron. Seeding the demo month and reading the profile found names
rendered in lower case. Asking the device nine ordinary questions found it
confabulating about a project it had never heard of. Piping four lines through
`you2 listen` found that it could not answer about something said thirty
seconds earlier.

**Disbelieve the comments.** Several docstrings here were true when written and
the code moved underneath them. Replay priority "can still draw an old
high-salience episode" — it cannot, the eligible pool is a median of 17 against
a limit of 600. "Unless something reactivates it" — nothing does. Prosody
"arousal is largely energy" — energy was normalised away before it was
measured, and returned 0.5196 across a 47× amplitude range.

**Watch the columns that move.** Probe precision is 1.00 in every ablation
variant, so it discriminates nothing; it is an alarm, not a signal. *Live
facts* is the one that caught a correction hijacking unrelated keys — it
should stay small and flat, and it silently went 14 → 90.

**And a 537-agent audit**, run across 75 lenses from memory neuroscience and
systems engineering, each finding then put through two independent attempts to
refute it. It produced 211 findings, 27 of which survived. It found the
durability bug above. It also produced four confident findings that did not
survive contact with the code, which is the reason nothing from it is
implemented without reading the source first.

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

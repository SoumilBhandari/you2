# Handoff

*For whoever picks this up next, including a future me with no memory of it.
Written 9 August 2026.*

This is the **operational** state: what exists, what works, what doesn't, and
what will bite you. It is not the design — that is
**[docs/brain.md](docs/brain.md)**, and you should read at least §1 and §17
before changing anything in `software/`.

---

## What this is

A desk voice assistant (`you2`, wake word **"Hey Friday"**) whose entire
premise is that it *remembers*. The hardware is designed and costed but not
built; the software is built and runs on a laptop with no hardware, no
network, and no model downloads.

The one-line thesis, and the thing every decision comes back to:

> The model is a download. The memory is the product.
> The weights are the cortex; the graph is the person.

---

## Repo map

| | |
|---|---|
| `docs/design.md` | hardware, enclosure, voice pipeline, BOM logic. **Predates the software.** |
| `docs/brain.md` | **the software design.** 20 sections. Read §1, §17 (standing decisions), §19 (corrections). |
| `docs/architecture.md`, `docs/bom.md` | hot path and parts, from the hardware phase |
| `software/` | everything that runs |
| `software/ui/` | the eye — 800×800 round display, canvas 2D, no dependencies |
| `deploy/` | `flash.sh` writes an SD card that boots into the eye; `install.sh` converges a running Pi |
| `cad/`, `diagrams/`, `hardware/` | the physical object |

### `software/you2/` — 14 packages, ~12k lines

```
config.py        every tunable, one frozen dataclass. NO magic numbers elsewhere.
clock.py         injectable time. Architecture, not a test helper — see below.

substrate/       neurons, synapses, in-RAM CSR graph, two-tier vectors, SQLite
activation/      base-level decay, spreading activation, inhibition, workspace
perception/      prosody, attention state machine, discourse, salience gate
encoding/        segmentation + binding. Makes NO model call.
consolidation/   the night: replay, abstraction, supersession, homeostasis,
                 forgetting, dream (REM), storage accounting
discovery/       contradiction, recurrence, open loops, bridging, insight scoring
inference/       engine (rules by cost), compile, tms, parsimony
person/          identity, rhythms, stance, style — who they are
audio/           wake word, VAD, endpointing, ASR, TTS, the hot loop
recall/          retrieve, memory-answerable answers, reconsolidation
llm/             OpenAI-compatible client, rule extractor, deterministic fake
runtime/         hot path, intent router, event bus, HTTP+SSE server, LEDs, CLI
eval/            synthetic history with ground truth, harness, ablations
```

---

## Running it

```bash
cd software
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

`numpy` is the only hard dependency. Everything else is stdlib on purpose —
this runs on a 2 GB Pi beside an audio stack, and every import costs RSS.

```bash
.venv/bin/pytest                      # 233 tests. ~4 min (the eval tests dominate)
.venv/bin/pytest --ignore=tests/test_eval.py   # ~25 s, what you want while iterating

.venv/bin/you2 demo                   # seed a month of life, open the eye
.venv/bin/you2 listen --fake --db /tmp/x.db -v   # ears+brain+voice, typed input
.venv/bin/you2 chat --fake
.venv/bin/you2 why "how is Anderson"  # print the WHOLE retrieval — use this constantly
.venv/bin/you2 night --fake
.venv/bin/you2 stats
.venv/bin/you2 eval --days 365        # simulate a year (~50 s)
.venv/bin/you2 eval --ablate          # ~6 min, all variants

.venv/bin/you2 extract                # is what goes INTO the store correct
.venv/bin/you2 extract --report       # every miss and trap, named
.venv/bin/you2 extract --live         # …through the real model, needs a key
.venv/bin/you2 extract "I'm at Lumen now"   # what would this one line store?
```

Global flags work before *or* after the subcommand. `--fake` swaps in the
deterministic model; without an API key it runs offline and says so.

---

## Current state

**Everything in `docs/brain.md` phases 1–9 is built and tested.** 233 tests,
offline, deterministic, no network or model downloads.

| file | tests | covers |
|---|---|---|
| `test_substrate.py` | 16 | store, graph, vectors, embedder, 10k-neuron scale gate |
| `test_activation.py` | 22 | spreading, base-level, inhibition, workspace, cues |
| `test_perception.py` | 22 | prosody, attention, discourse, salience |
| `test_encoding.py` | 21 | segmentation, binding, loops, rule extraction |
| `test_consolidation.py` | 21 | supersession, homeostasis, forgetting, the night |
| `test_inference.py` | 16 | graph rules, compilation, TMS, parsimony |
| `test_person.py` | 15 | rhythms, stance, style, the persona block |
| `test_audio.py` | 20 | wake word, VAD, endpointing, barge-in, the loop |
| `test_runtime.py` | 17 | the whole turn, routing, reconsolidation |
| `test_experience.py` | 15 | event bus, cascade scene, server, LEDs |
| `test_extraction.py` | 38 | **the extraction corpus**, the validator, key discipline |
| `test_eval.py` | 10 | harness + **ablation claims** (slow) |

### Measured

One simulated year, 1,178 utterances, 353 nights, ~50 s per run:

| day | precision | focus | diversity | stale | neurons | edges |
|---|---|---|---|---|---|---|
| 30 | 0.50 | 0.73 | 0.88 | 0.00 | 119 | 692 |
| 365 | **1.00** | 0.89 | 0.91 | **0.00** | 1,085 | 2,084 |

Precision *rises* with elapsed time and superseded values never reach the
block. **That curve is the primary regression test of the whole project.** If
it ever falls, consolidation has broken.

And extraction, against 24 hand-labelled sessions (`you2 extract`):

| | precision | recall | key stability | traps | grounded |
|---|---|---|---|---|---|
| rules | 1.00 | 1.00 | 0.67 | 0/22 | 0.89 |
| a sloppy model, raw | 0.66 | 1.00 | **0.00** | 4/22 | 0.64 |
| the same model, checked | 1.00 | 1.00 | **0.67** | 0/22 | 0.89 |

The stability column is what the validator is for. Unchecked, every dimension
gets a fresh key, so supersession never fires and the store keeps every
version of every preference forever.

Ablation deltas at one year (`you2 eval --ablate`):

| removing | costs |
|---|---|
| fan effect | focus 0.88 → 0.30, **visited 14 → 407** |
| inhibition | diversity 0.82 → 0.35 |
| spreading (top-k only) | diversity 0.82 → 0.23 |
| base-level decay | **stale leakage 0.00 → 0.50** |
| downscaling | edges 2,181 → 4,658 |
| the block cap | tokens 130 → 423, zero quality gain |
| **the salience gate** | **nothing measurable** |

---

## Things that will bite you

### 1. Time is injected. Always.

Nothing calls `time.time()`. Everything takes a `Clock`. `SimClock` is what
lets the eval harness run two years in ninety seconds, and a memory system
whose core mechanisms are decay and nightly cycles cannot be tested any other
way. **Any module that reaches for the wall clock is a bug.**

### 2. `store.save()` writes the whole row

The workspace holds `Neuron` objects loaded *before* the turn began. A repair
that invalidates a fact mid-turn was silently reverted by the broadcast at the
end of the same turn, because broadcast wrote back a pre-repair snapshot. Use
`store.touch()` for access updates. If you add another write path, make it
narrow.

### 3. The salience gate suppresses exactly the things you need

This bug has now appeared **three times** in different clothes, and it will
appear again:

- A recurring complaint is unsurprising → recurrence could never fire
- A closure ("sent it, that's done") is unsurprising *by construction* → open
  loops never closed
- A repeated statement is unsurprising → supersession never saw it

The gate is right that we already knew something and wrong that it therefore
does not matter. When you add a mechanism that depends on repeated or
confirmatory utterances, **check whether the gate eats them first.**

### 4. Fact keys must capture the dimension, not the value

`user/pref_milk`, not `user/pref_oat_milk`. If the key encodes the value,
supersession can never fire and the store keeps every version forever.
`rules.topic()` cuts at the first prepositional attachment for this reason —
"meetings in the morning" and "meetings in the afternoon these days" have to
key the same.

### 5. Nothing unabsorbed is ever deleted

`forgetting.audit()` and `tms.audit()` must both return empty, and the night
asserts it. If you add a destructive path, gate it the same way.

### 6. Derived claims must be labelled derived

Including compiled inferences. Without the label the model asserts an
inference back to the person as something they told it. `workspace.render()`
prefixes `(inferred)`; don't let a new code path around it.

### 7. The write path makes no model call

Deliberate. It works offline, adds no latency variance, and cannot fail a
turn. Entity extraction is rules; facts are extracted at night where there is
context and time. Do not move model calls earlier.

### 8. Nothing an extractor produces is trusted, the rules included

Everything goes through `llm/validate.py` before it reaches the store. If you
add a path that writes facts, entities or loops without it, you have added a
hole. The order of checks is cheapest-first and deliberate; the one people
get backwards is **grounding with a quote**, which makes the check *stricter*
— the claim is graded against the quote, not the whole batch. A verbatim span
proves the model read something real, not that the claim follows from it.

### 9. A key that encodes the value can never be superseded

`user/pref_oat_milk` is not a near miss for `user/pref_milk` — it is a second
row that will sit beside the first forever. Both the prompt and
`canonical_key()` exist for this. When you touch either, run `you2 extract`
and watch **stability** and **collisions** together: stability alone is
trivially gamed by snapping everything onto one key, which destroys the store
in the opposite direction.

---

## Honest gaps

**Extraction is measured, but not against a real model.** `you2 extract`
scores 24 hand-labelled sessions, and `eval/sloppy.py` stands in for a model
that is wrong the way real models are wrong. That is a genuine instrument and
it caught real defects. What it still cannot tell you is how a *particular* 8B
model behaves on *your* speech: `you2 extract --live` is one command and needs
a key, and until someone runs it that column is empty.

Two things to hold in mind when reading the 1.00s. The corpus is mine and I
fixed what it found — the rule held to was that a fix must be a whole
construction, never a patch for one sentence, but the number is still
generous. And one labelled failure is deliberately left standing: "I only ever
buy semi-skimmed" said after "we're out of milk" is about milk, and no rule
recovers a head noun that was never spoken. It is what keeps rules stability
at 0.67, and it is exactly the gap a real model should close.

**Bridging (analogy) reports as untested, not working.** The fake declines to
judge analogies because a rule-based judge genuinely cannot. Whether structure
mapping finds anything real at personal scale is still open — see brain.md
§18. Measure it before defending it.

**The salience gate is not earning its complexity** on the current corpus.
Two caveats before deleting it: the synthetic chatter may be unrealistically
easy to filter, and the gate also sets replay priority, which this corpus does
not stress. The burden of proof is on the gate.

**No hardware has been touched.** No microphone, no display, no LEDs, no Pi.
The acoustic half of barge-in — whether AEC holds with a speaker 60 mm from a
mic — is untestable until parts exist and is ranked the highest risk in
design.md. The orchestration half is tested.

**The hash embedder is a lexical model**, not a semantic one. It makes the
tests deterministic and offline, which is worth a great deal, but probe
results are bounded by it. `OnnxEmbedder` (MiniLM) is written and behind the
same protocol; swapping it is one line and will change the eval numbers.

**Prosodic arousal is unvalidated.** A reSpeaker at 2 m with AGC may flatten
exactly the dynamic range the estimate depends on.

---

## Suggested next work

Roughly in order of value:

1. **`you2 extract --live`.** One command, one key, and the empty column in
   the table above is filled. Do this before anything else — it costs
   minutes and it tells you whether the model beats the rules at all, which
   changes what is worth working on next.
2. **Then real transcripts.** Run `you2 chat` for a week and read `you2
   night`'s journal. The corpus is 24 sessions I wrote; a week of your own
   speech will contain constructions it does not, and every one of those is a
   session to add.
3. **Re-run the eval with `OnnxEmbedder`** and see how much of the current
   precision was the lexical embedder.
4. **Resolve the salience gate** — either find the corpus where it matters or
   delete it.
5. **Rating loop UI.** The instrument exists (`store.rate`, `discovery.verdict`,
   `store.precision_by_type`) but nothing surfaces insights for a thumbs
   up/down yet. After three months of real use that table is the only real
   evidence about which connection types are worth keeping.
6. **Order the reSpeaker.** Longest lead, highest risk, and barge-in cannot be
   tested any other way.
7. **Person model against real history** — rhythms and stance need months of
   genuine data before their thresholds mean anything.

---

## Conventions

- **Commit everything, with a message that says why.** The commit log here is
  a design record; several entries document bugs and corrected claims that
  are not written down anywhere else.
- **Correct claims in place.** `docs/brain.md` §19 is a corrections log of
  things believed and then disproved. When a measurement contradicts a
  comment, fix the comment.
- **State sample sizes.** Especially in `person/` — every claim about the user
  carries `n`, and thin evidence produces an empty section rather than a
  hedged one.
- **Never silently truncate.** Every cap that drops work logs what it dropped.
- Tests assert *claims*, not numbers, so tuning a constant doesn't fail them
  but removing a mechanism's effect does.

---

## Repo

`github.com/SoumilBhandari/you2` — private. `main`, 16 commits, clean.

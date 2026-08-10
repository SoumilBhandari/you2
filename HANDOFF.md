# Handoff

*For whoever picks this up next, including a future me with no memory of it.
Written 9 August 2026.*

This is the **operational** state: what exists, what works, what doesn't, and
what will bite you. For `software/` in detail — current numbers, what the
last session changed and why, and what is still in flight — see
**[handoff_software.md](handoff_software.md)**. It is not the design — that is
**[docs/brain.md](docs/brain.md)**, and you should read at least §1 and §17
before changing anything in `software/`.

For the physical object in the same detail — the enclosure, the print plates,
the parts list and what will bite you there — see
**[handoff_hardware.md](handoff_hardware.md)**.

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
| `cad/` | `you2-revD.scad` is the enclosure; `check.py` and `fit.py` are its gates |
| `hardware/` | wiring, assembly, and what to measure before you buy |
| `docs/build.html` | **the build document** — every part, plate, fastener and step |
| `diagrams/` | system architecture and the rev C general-arrangement drawing |

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
.venv/bin/pytest                      # 279 tests. ~4 min (the eval tests dominate)
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

**Everything in `docs/brain.md` phases 1–9 is built and tested.** 279 tests,
offline, deterministic, no network or model downloads.

| file | tests | covers |
|---|---|---|
| `test_substrate.py` | 16 | store, graph, vectors, embedder, 10k-neuron scale gate |
| `test_activation.py` | 22 | spreading, base-level, inhibition, workspace, cues |
| `test_perception.py` | 22 | prosody, attention, discourse, salience |
| `test_encoding.py` | 21 | segmentation, binding, loops, rule extraction |
| `test_consolidation.py` | 23 | supersession, homeostasis, forgetting, the night |
| `test_inference.py` | 16 | graph rules, compilation, TMS, parsimony |
| `test_person.py` | 15 | rhythms, stance, style, the persona block |
| `test_audio.py` | 20 | wake word, VAD, endpointing, barge-in, the loop |
| `test_runtime.py` | 17 | the whole turn, routing, reconsolidation |
| `test_experience.py` | 15 | event bus, cascade scene, server, LEDs |
| `test_extraction.py` | 60 | **the extraction corpus**, the validator, key discipline, corrections |
| `test_eval.py` | 10 | harness + **ablation claims** (slow) |

### Measured

One simulated year, 1,178 utterances, 353 nights, ~50 s per run:

| day | precision | focus | diversity | stale | neurons | live facts | edges |
|---|---|---|---|---|---|---|---|
| 30 | 0.50 | 0.73 | 0.88 | 0.00 | 119 | 8 | 692 |
| 365 | **1.00** | 0.88 | 0.92 | **0.00** | 1,078 | 14 | 2,004 |

And two years (`you2 eval --days 730`, 2,325 utterances, 707 nights, ~143 s),
because "does retrieval hold at month 24" is the question the product is
built around and it is cheap to actually ask:

| day | precision | focus | diversity | stale | neurons | live facts | edges |
|---|---|---|---|---|---|---|---|
| 730 | **1.00** | 0.91 | 0.68 | **0.00** | 2,077 | 11 | 2,743 |

On disk that is **8.9 MB**, which is the number that settles the storage
question the whole design rests on.

Precision *rises* with elapsed time, superseded values never reach the block,
and after two years of daily use the store is 2,070 neurons holding 14 live
facts. **That curve is the primary regression test of the whole project.** If
it ever falls, consolidation has broken.

The live-facts column is worth watching as closely as precision. It should
stay small and roughly flat — the planted history only ever has a dozen or so
durable claims in it. When it jumped to 90 it was a correction hijacking
unrelated keys, and nothing else in the suite noticed.

And extraction, against 24 hand-labelled sessions (`you2 extract`):

| | precision | recall | key stability | traps | grounded |
|---|---|---|---|---|---|
| rules | 1.00 | 1.00 | **1.00** | 0/22 | 0.89 |
| a sloppy model, raw | 0.66 | 1.00 | **0.00** | 4/22 | 0.64 |
| the same model, checked | 1.00 | 1.00 | **1.00** | 0/22 | 0.89 |

The stability column is what the validator is for. Unchecked, every dimension
gets a fresh key, so supersession never fires and the store keeps every
version of every preference forever.

Ablation deltas at one year (`you2 eval --ablate`, ~8 min, ~60 s a variant):

| removing | costs |
|---|---|
| fan effect | focus 0.89 → 0.34, **visited 14 → 380** |
| inhibition | **diversity 0.91 → 0.33** |
| spreading (top-k only) | diversity 0.91 → 0.38, and the *highest* focus of any variant |
| base-level decay | **stale leakage 0.00 → 0.50** |
| downscaling | edges 2,024 → 4,283 |
| the block cap | tokens 132 → 400, focus 0.89 → 0.84 — pure cost |
| **the salience gate** | **nothing but storage** |

Probe precision is 1.00 in every variant, which is why it is not the column
to read. `top-k-only` scoring the best focus and nearly the worst diversity is
naive RAG's failure mode measured rather than asserted: it returns the most
on-topic set and the least informative one.

The salience gate's case has got weaker, not stronger. Its only measured
benefit was ever storage, and two years of daily use is **8.9 MB on disk**
against a 512 MB budget — so it is buying something that is not scarce, while
having caused four separate bugs (HANDOFF traps #3 and #10). The burden of
proof was on it before; it has not met it.

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
hole — and there was one: `recall/reconsolidate.py` wrote corrections
straight in, so "no, I drink semi-skimmed now" was stored verbatim, in the
first person, under the milk key, ready to be read back aloud.

The order of checks is cheapest-first and deliberate. The one people get
backwards is **grounding with a quote**, which makes the check *stricter* —
the claim is graded against the quote, not the whole batch. A verbatim span
proves the model read something real, not that the claim follows from it.

### 9. A key that encodes the value can never be superseded

`user/pref_oat_milk` is not a near miss for `user/pref_milk` — it is a second
row that will sit beside the first forever. Both the prompt and
`canonical_key()` exist for this. When you touch either, run `you2 extract`
and watch **stability** and **collisions** together: stability alone is
trivially gamed by snapping everything onto one key, which destroys the store
in the opposite direction.

### 10. A correction can reach further than you think

`recall/reconsolidate.py` picks the memory a correction is aimed at, and then
puts the replacement **under that memory's key**. Get the aim wrong and you
do not merely miss — you invalidate a live fact and file an unrelated claim
in its place.

It ranked candidates by activation and similarity together, so the most
*active* memory could win against a total semantic mismatch. "The coffee
machine is making that noise again / actually I prefer oat milk now" filed the
milk preference as `coffee_machine/state`. Every later mention of the coffee
machine then failed to match its own holder and forked a new key — forty of
them by day 365, live facts 14 → 90, suite green throughout.

The guard is on the **subject, not the wording**, and the distinction is the
whole lesson. A lexical floor catches the coffee machine and breaks "no, I
drink semi-skimmed now" against a stored "prefers dairy milk" — same
dimension, almost no shared vocabulary, an entirely ordinary repair. If you
touch this path, test both.

### 11. A loop that reports success can still be dropping work

Three separate defects in one session, all the same shape: a stage did less
than it claimed and said nothing.

- `night.run` processed `len(batches) // cycles` batches and stopped at the
  cycle cap, abandoning the tail — **always the most recent**, because
  batches are chronological. Five nights running, the demo month's "actually
  I prefer meetings in the afternoon" was drawn, dropped, and never
  consolidated, while the report said "5 batches".
- `_score_discovery` reported `len(journal(..., limit=500))` as a count, so
  every run printed exactly 500. The real figure was 1,101.
- `supersede._disambiguate` forked `key_2 … key_39` and then absorbed
  everything into `key_x`.

When you write a loop with a cap, a limit or a budget, make it say what it
dropped. `ripple_batches` already does (`replay_truncated`); that is the
pattern. **A green suite cannot see any of this** — the work still happens,
just less of it.

### 12. The memory path can confabulate, and it is the fastest path in the system

`recall/answer.py` answers without a model, in the user's own vocabulary, in
about 20 ms. That is its value and its danger. It reported whatever was in
the workspace, and retrieval *always returns something* — the ambient cues
alone guarantee a non-empty workspace. So "what did I say about the Halloway
report", a project this store had never heard of, was answered with a
confident sentence about the user's sister and cilantro.

Anything added to this file must filter to material actually about the topic
asked, and must be willing to say **nothing**. `docs/brain.md` calls guessing
about someone's life the one unrecoverable failure; this path was doing it by
construction.

---

## Honest gaps

**Extraction is measured, but not against a real model.** `you2 extract`
scores 24 hand-labelled sessions, and `eval/sloppy.py` stands in for a model
that is wrong the way real models are wrong. That is a genuine instrument and
it caught real defects. What it still cannot tell you is how a *particular* 8B
model behaves on *your* speech: `you2 extract --live` is one command and needs
a key, and until someone runs it that column is empty.

One thing to hold in mind when reading the 1.00s: the corpus is mine and I
fixed what it found. The rule held to was that a fix must be a whole
construction, never a patch for one sentence — but the number is still
generous, and a corpus you patch sentence by sentence measures your patience
rather than your extractor.

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

# Software handoff

*The software half, in detail. Written 10 August 2026, at a deliberate pause.*

[HANDOFF.md](HANDOFF.md) is the whole project — repo map, hardware context,
run commands, the standing traps. **Read that first.** This is the companion
for whoever picks up `software/` specifically: what the numbers are today,
what changed in the last session and why, what is still in flight, and the
handful of things that will waste your afternoon if nobody tells you.

Design lives in [docs/brain.md](docs/brain.md). Read §1, §15 (evaluation),
§17 (standing decisions) and §19 (the corrections log) before changing
anything — §19 in particular, because it is a list of things that were
believed, measured, and turned out to be false, and it will save you from
re-deriving several of them.

---

## Where it is

Everything in `docs/brain.md` phases 1–9 is built and tested. **284 tests**,
offline, deterministic, no network and no model downloads.

| file | tests | covers |
|---|---|---|
| `test_extraction.py` | 68 | the labelled corpus, the validator, key discipline, corrections |
| `test_consolidation.py` | 28 | supersession, homeostasis, forgetting, the night |
| `test_encoding.py` | 23 | segmentation, binding, loops, episode caps |
| `test_perception.py` | 23 | prosody, attention, discourse, salience |
| `test_activation.py` | 22 | spreading, base-level, inhibition, workspace, cues |
| `test_runtime.py` | 22 | the whole turn, routing, reconsolidation, working memory |
| `test_audio.py` | 20 | wake word, VAD, endpointing, barge-in, the loop |
| `test_substrate.py` | 19 | store, graph, vectors, embedder identity, scale |
| `test_inference.py` | 19 | graph rules, compilation, TMS, parsimony |
| `test_experience.py` | 15 | event bus, cascade scene, server, LEDs |
| `test_person.py` | 15 | rhythms, stance, style, the persona block |
| `test_eval.py` | 10 | harness + ablation claims (slow) |

```bash
cd software
.venv/bin/pytest --ignore=tests/test_eval.py   # ~30 s, what you want while iterating
.venv/bin/pytest                               # 284, ~4 min
```

### Measured

Extraction, against 24 hand-labelled sessions (`you2 extract`):

| | precision | recall | key stability | traps | grounded |
|---|---|---|---|---|---|
| rules | 1.00 | 1.00 | **1.00** | 0/22 | 0.89 |
| a sloppy model, raw | 0.66 | 1.00 | **0.00** | 4/22 | 0.64 |
| the same model, checked | 1.00 | 1.00 | **1.00** | 0/22 | 0.89 |

Retrieval over simulated history (`you2 eval --days 730`):

| day | precision | focus | diversity | stale | neurons | live facts |
|---|---|---|---|---|---|---|
| 365 | **1.00** | 0.89 | 0.91 | **0.00** | 1,078 | 11 |
| 730 | **1.00** | 0.91 | 0.68 | **0.00** | 2,077 | 11 |

Two years of daily use is **8.9 MB on disk**. Hot-path retrieval is **0.52 ms
at 100k neurons** and flat across a 10× range.

Ablation deltas at one year (`you2 eval --ablate`):

| removing | costs |
|---|---|
| fan effect | focus 0.89 → 0.34, visited 14 → 380 |
| inhibition | diversity 0.91 → 0.33 |
| spreading (top-k only) | diversity 0.91 → 0.38, and the *highest* focus of any variant |
| base-level decay | stale leakage 0.00 → 0.50 |
| downscaling | edges 2,024 → 4,283 |
| the item cap (12 → 40) | tokens 132 → 251 |
| **the token budget (400 → 4000)** | **nothing at all** |
| **the salience gate** | **nothing at all** |

**Three columns to watch, and they are not the obvious ones.** Probe precision
is 1.00 in every variant, so it discriminates nothing — it is a regression
alarm, not a signal. What moves is *focus*, *diversity*, and **live facts**,
which should stay small and roughly flat; when it jumped to 90 that was a
correction hijacking unrelated keys, and nothing else in the suite noticed.

---

## What changed in the last session

Thirty-two commits. Two of them added instruments; the rest are what the
instruments found.

**The extraction benchmark** (`you2 extract`) is new and is the reason most
of the rest exists. 24 hand-labelled sessions, 21 gold facts, and **22 traps**
— questions, hypotheticals, world knowledge, other people's opinions, things
true for an afternoon. `eval/sloppy.py` is a deterministic model that is wrong
the way real models are wrong, so what `llm/validate.py` buys is a measurement
rather than an assertion.

Seventeen bugs, in rough order of severity:

1. **A correction could hijack an unrelated memory.** `reconsolidate.target`
   ranked by activation, so the most *active* memory won against a total
   semantic mismatch — and the replacement was filed under its key. Live facts
   went 14 → 90. The guard belongs on the **subject, not the wording**.
2. **The last batch of every night was silently abandoned.** Integer division
   in `night.run`. Batches are chronological, so the dropped one was always
   the most recent — a revised preference was drawn and dropped five nights
   running while the report said "5 batches".
3. **"Forget that" left the words in four places** — the extracted entity, the
   batch-mates' gist, `sessions.summary`, and the journal.
4. **The memory path confabulated.** Asked about a project it had never heard
   of, it answered confidently about the user's sister and cilantro.
5. **Swapping the embedder silently mixed two vector spaces** — dimensionality
   is not an identity, and both embedders are 384.
6. **Loudness never reached the arousal estimate.** `extract` peak-normalised
   before computing RMS, so the 0.35-weighted term returned 0.5196 across a
   47× amplitude range.
7. **Compaction left vectors describing deleted text**, so compacted episodes
   rendered as identical lines that inhibition structurally could not see.
8. **A broken inference chain survived** because most of its premises did.
9. **Nominal ellipsis** — "I only ever buy semi-skimmed" after "we're out of
   milk". Key stability 0.67 → 1.00.
10. **Recurrence reported a confounded pattern.** Added Kamin blocking; false
    positives 1 → 0.
11. **No cap on episode length** — one paragraph became a 102 KB neuron.
12. **It could not answer about what you just said.** Added a working-memory
    buffer over the open segment.
13. **A repeated claim could fork one key forty times.**
14. **`by_kind(... ORDER BY ...)` had no composite index** — 56 ms per turn.
15. **"500 rejected" was a query limit**, not a count. Real figure 1,101.
16. **Names lost their capitals** in the profile block.
17. **`Kind.AFFECT`** was wired into five sites and constructed nowhere.

And three documented claims corrected against measurement, plus **two proposed
changes measured and rejected** — all six in `docs/brain.md` §19.

---

## Things that will waste your afternoon

The seven original traps are in [HANDOFF.md](HANDOFF.md) and still apply.
These are the ones the last session added.

### A loop that reports success can still be dropping work

Three separate defects were this same shape: the night abandoning its last
batch, a query limit reported as a count, and a key forking forty times. **A
green suite cannot see any of them** — the work still happens, just less of
it. If you write a loop with a cap, a limit or a budget, make it say what it
dropped. `replay.ripple_batches` does (`replay_truncated`); that is the
pattern.

### The memory path can confabulate, and it is the fastest path in the system

`recall/answer.py` answers with no model, in the user's own vocabulary, in
about 20 ms. Retrieval *always returns something* — ambient cues alone
guarantee a non-empty workspace — so anything added here must filter to
material actually about the topic asked, and must be willing to say nothing.

### A correction reaches further than you think

`reconsolidate` puts the replacement under the **victim's key**. Aim it wrong
and you invalidate a live fact and file an unrelated claim in its place. The
guard is on the subject, not the wording: a lexical floor catches the coffee
machine and breaks "no, I drink semi-skimmed now" against "prefers dairy
milk", which is an entirely ordinary repair with almost no shared vocabulary.

### Dimensionality is not an embedder identity

`HashEmbedder` and MiniLM are both 384. `substrate.embed.fingerprint()` now
stamps the store and re-embeds on mismatch. If you add a third embedder, do
not weaken that check.

### Docstrings here have been wrong in load-bearing ways

Not sloppiness — they were true when written and the code moved. Several of
this session's bugs were found by disbelieving a comment: replay priority
"can still draw an old high-salience episode" (it cannot), "unless something
reactivates it" (nothing does), prosody "arousal is largely energy" (energy
was not in it), the 400-token cap "is the workspace capacity" (it is inert).
**When a comment makes a claim, measure it.**

---

## Honest gaps

**Extraction has never met a real model.** `you2 extract --live` is one
command and needs `YOU2_API_KEY`. Until someone runs it, that column is empty
and every extraction number here is the rule extractor's.

**The corpus is mine and I fixed what it found.** The rule held to was that a
fix must be a whole construction, never a patch for one sentence — but 1.00
across the board should still be read with suspicion.

**Bridging (analogy) reports as untested, not working.** The fake declines
because a rule-based judge genuinely cannot evaluate an analogy.

**The salience gate still earns nothing.** Focus, diversity, stale leakage and
tokens all move by 0.00 without it, its only benefit was ever storage, and
storage is 8.9 MB against a 512 MB budget. It has meanwhile been the proximate
cause of four defects, because the material it suppresses — repetition,
closure, confirmation, correction — is exactly what recurrence, loop closure,
supersession and reconsolidation each need. The burden of proof has been on it
for a long time. **It is the first thing I would delete.**

**The hash embedder bounds every number here.** `OnnxEmbedder` is written and
behind the same protocol; swapping it is now safe (see the fingerprint) and
will change the eval numbers.

**No hardware has been touched.** No microphone, no display, no Pi.

---

## In flight

### The multi-agent audit

A 75-lens audit is **paused mid-run**, with 152 agent results cached. Resume:

```bash
Workflow({scriptPath: "<scratchpad>/brain-audit.js", resumeFromRunId: "wf_ba23e852-da4"})
```

Cached agents replay instantly; only the unfinished ones run. The first
attempt died on a session limit with the verification and synthesis phases
lost, which is why nothing in it should be treated as confirmed.

**Findings verified and fixed:** the compaction re-embed, "forget that", the
prosody normalisation, the embedder identity, `Kind.AFFECT`, the inert token
budget, inert replay priority.

**Findings checked and rejected — four so far:**

- *"`Brain._context` survives session close"* — `close()` clears it at
  pipeline.py:133, and a six-hour lapse leaves it empty.
- *"spread.py discards convergent activation"* — line 161 accumulates it for
  nodes not yet fired, which is the case that matters. Only already-fired
  nodes are skipped, and those had already cleared threshold.
- *"prioritised replay never selects"* — true in the operating regime, false
  as an absolute: priority binds fine at 1,500 eligible episodes.
- *CLS says interleave old with new* — implemented and measured: precision
  1.00 → 0.75, stale leakage 0.00 → **1.00**. In a store that supersedes,
  re-consolidating an old episode resurrects retired facts.

**Do not implement from the finding list without reading the code.** Four of
the first eight high-confidence findings did not survive contact with it.

### Unverified, worth doing next

- **The cue weights** (`activation/cues.py:28-32`) are module constants, so
  `eval --ablate` structurally cannot touch them — and they are the largest
  lever in retrieval. An audit agent measured `W_CONTEXT=0` at focus +0.077,
  diversity −0.271. Move them into `BrainConfig` and add the ablations before
  changing any value.
- **Block stickiness.** The same agent measured mean Jaccard 0.727 between
  consecutive in-session blocks, 12.4% byte-identical. If that holds, the
  device says the same thing about you turn after turn and no metric can see
  it. Add the measurement first.

---

## Conventions

- **Commit everything, with a message that says why.** The log here is a
  design record; several bugs and corrected claims are not written down
  anywhere else.
- **Correct claims in place.** `docs/brain.md` §19 is the corrections log,
  and it includes changes measured and *not* made — those are worth as much
  as the ones that shipped.
- **Measure before changing, and after.** Every mechanism in the ablation
  table earned its place there.
- **State sample sizes**, especially in `person/`.
- **Never silently truncate.** Every cap that drops work logs what it dropped.
- Tests assert *claims*, not numbers, so tuning a constant does not fail them
  but removing a mechanism's effect does.
- **Do not use `git reset --hard` in this repo.** It discards uncommitted
  work in the tree, including other people's. It has already cost this project
  two files.

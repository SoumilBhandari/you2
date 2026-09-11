# Software handoff

*The software half, in detail. Updated 10 August 2026.*

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

Everything in `docs/brain.md` phases 1–9 is built and tested. **286 tests**,
offline, deterministic, no network and no model downloads.

| file | tests | covers |
|---|---|---|
| `test_extraction.py` | 68 | the labelled corpus, the validator, key discipline, corrections |
| `test_consolidation.py` | 28 | supersession, homeostasis, forgetting, the night |
| `test_encoding.py` | 23 | segmentation, binding, loops, episode caps |
| `test_perception.py` | 23 | prosody, attention, discourse, salience |
| `test_activation.py` | 22 | spreading, base-level, inhibition, workspace, cues |
| `test_runtime.py` | 24 | the whole turn, routing, reconsolidation, working memory, **durability under kill** |
| `test_audio.py` | 20 | wake word, VAD, endpointing, barge-in, the loop |
| `test_substrate.py` | 19 | store, graph, vectors, embedder identity, scale |
| `test_inference.py` | 19 | graph rules, compilation, TMS, parsimony |
| `test_experience.py` | 15 | event bus, cascade scene, server, LEDs |
| `test_person.py` | 15 | rhythms, stance, style, the persona block |
| `test_eval.py` | 10 | harness + ablation claims (slow) |

```bash
cd software
.venv/bin/pytest --ignore=tests/test_eval.py   # ~30 s, what you want while iterating
.venv/bin/pytest                               # 286, ~4 min
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
| 365 | 0.50 | 0.73 | 0.94 | **0.00** | 1,065 | 7 |
| 730 | **1.00** | 0.91 | 0.70 | **0.00** | 2,077 | 11 |

**Read that precision column with the caveat two sections down before you
quote it anywhere.** The day-365 row here used to read 1.00 / 0.89 / 0.91 /
1,078 / 11, which is a *365-day* run's final row spliced into the two-year
table — the same mistake the caveat explains. `software/README.md` has always
had the internally consistent version of this table.

Two years of daily use is **8.9 MB on disk**. Hot-path retrieval is **0.52 ms
at 100k neurons** and flat across a 10× range. A turn is p50 2.3 ms, p95
3.7 ms, and **is committed before it returns** — see below. Note what that
does and does not buy: everything *committed* survives a kill, but only sealed
episodes are ever written, so a hard kill still loses the open segment (up to
`MAX_TURNS = 12` turns / `MAX_SPAN = 900 s`). The 60-turn table in the README
reads better than reality because its `SimClock` seals an episode every turn;
on the real clock, 25 turns is 2 episodes.

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

### The precision curve does not measure what the docs say it measures

It is not even the regression alarm the paragraph above calls it. The
0.50 → 1.00 rise, which `software/README.md` calls *"the one chart that
matters"* and `harness.py` calls the primary regression test of the project,
is a property of the corpus generator and not of the store.

`eval/synthetic.py:168` plants every preference change at
`change_day = int(days * 0.55) + i * 3`, and `truth.probes` asks for the *new*
values — `oat`, `lumen`, `ashcombe`, `seated`. So before that day the four
preference probes ask for tokens that have never been uttered, and the store
cannot answer them no matter how well it works. Four of eight probes fail by
construction: precision 0.50. After that day, 1.00. The step is the moment the
generator speaks, not the moment consolidation succeeds.

Two measurements, either one sufficient:

- **The threshold moves with the run length.** Day 180 scores 0.50 inside a
  365-day run and 1.00 inside a 180-day run — same seed, same day index,
  opposite result. In a 730-day run the changes land on days 401–410, which
  is why day 365 above is 0.50 and why the old spliced row was detectable.
- **It does not need the brain.** With `downscale_rho=1.0`,
  `prune_weight_min=0.0`, `spread_decay=0.0`, `max_hops=0`, `base_decay=0.0`
  and `inhibition=0.0` — downscaling, spreading, base-level decay and
  inhibition all off at once — the curve is unchanged: 0.50, 0.50, 0.50, 1.00.

So the headline chart cannot fall, which means it cannot regress, which means
it tests nothing. **Stale leakage is the column that was doing the real work
all along**: it is 0.00 throughout and it *does* break when you remove
base-level decay (0.00 → 0.50), which is a genuine claim about supersession.

Not fixed here, because the fix is a design decision rather than a patch:
probe for the value that is *current as of each checkpoint* instead of the
end-state value, so that early checkpoints ask answerable questions and the
curve can actually go down when the store gets worse. Whoever does that should
expect every published precision number in every doc to change.

---

## What changed in the last session

Thirty-four commits. Two of them added instruments; the rest are what the
instruments found.

**The extraction benchmark** (`you2 extract`) is new and is the reason most
of the rest exists. 24 hand-labelled sessions, 21 gold facts, and **22 traps**
— questions, hypotheticals, world knowledge, other people's opinions, things
true for an afternoon. `eval/sloppy.py` is a deterministic model that is wrong
the way real models are wrong, so what `llm/validate.py` buys is a measurement
rather than an assertion.

Eighteen bugs, in rough order of severity:

1. **A restart threw away every memory since the last night.** `sqlite3` opens
   an implicit deferred transaction on the first write, and `store.flush()`
   was the only commit — reached by `Brain.close()`, `store.close()` and the
   night, none of which a turn touches. 60 turns gave 61 neurons and 181 edges
   in process and **0 and 0** on disk after `os._exit(9)`. Under the shipped
   unit (`Restart=always`, no signal handler) that is every reboot and every
   OOM kill; the 05:30 backup then faithfully backed up a store missing the
   day. "Forgotten." was not durable either.
2. **A correction could hijack an unrelated memory.** `reconsolidate.target`
   ranked by activation, so the most *active* memory won against a total
   semantic mismatch — and the replacement was filed under its key. Live facts
   went 14 → 90. The guard belongs on the **subject, not the wording**.
3. **The last batch of every night was silently abandoned.** Integer division
   in `night.run`. Batches are chronological, so the dropped one was always
   the most recent — a revised preference was drawn and dropped five nights
   running while the report said "5 batches".
4. **"Forget that" left the words in four places** — the extracted entity, the
   batch-mates' gist, `sessions.summary`, and the journal.
5. **The memory path confabulated.** Asked about a project it had never heard
   of, it answered confidently about the user's sister and cilantro.
6. **Swapping the embedder silently mixed two vector spaces** — dimensionality
   is not an identity, and both embedders are 384.
7. **Loudness never reached the arousal estimate.** `extract` peak-normalised
   before computing RMS, so the 0.35-weighted term returned 0.5196 across a
   47× amplitude range.
8. **Compaction left vectors describing deleted text**, so compacted episodes
   rendered as identical lines that inhibition structurally could not see.
9. **A broken inference chain survived** because most of its premises did.
10. **Nominal ellipsis** — "I only ever buy semi-skimmed" after "we're out of
   milk". Key stability 0.67 → 1.00.
11. **Recurrence reported a confounded pattern.** Added Kamin blocking; false
    positives 1 → 0.
12. **No cap on episode length** — one paragraph became a 102 KB neuron.
13. **It could not answer about what you just said.** Added a working-memory
    buffer over the open segment.
14. **A repeated claim could fork one key forty times.**
15. **`by_kind(... ORDER BY ...)` had no composite index** — 56 ms per turn.
16. **"500 rejected" was a query limit**, not a count. Real figure 1,101.
17. **Names lost their capitals** in the profile block.
18. **`Kind.AFFECT`** was wired into five sites and constructed nowhere.

And three documented claims corrected against measurement, plus **two proposed
changes measured and rejected** — all six in `docs/brain.md` §19.

---

## Things that will waste your afternoon

The seven original traps are in [HANDOFF.md](HANDOFF.md) and still apply.
These are the ones the last session added.

### Nothing is on disk until something commits

`store.flush()` is the only commit in the system, and for most of this
project's life the turn path never reached it. If you add a write path, ask
what commits it — and if you add a *long* one, guard it: `flush()` is a no-op
inside `transaction()` because `persist_graph` deletes every edge and
re-inserts them on the night thread, and a commit landing in that window makes
the empty table durable and beyond rollback.

Anything claiming durability must be tested by killing a subprocess.
`close()`, `__del__` and `atexit` each hide this bug, and the suite was green
through all of it.

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

### The multi-agent audit — finished

**537 agents, 535 completed, 31.8M tokens, 11,432 tool uses, 7.8 hours.**
75 lenses across memory neuroscience and systems engineering, each finding
put through two independent attempts to refute it, then an implementation
design for what survived. **211 findings, 27 survived**, merged to 21.

Its own headline: *a day of memory lost to any restart, a correction guard
that is a no-op for the 71% of facts that are about the person, and compiled
inferences spoken as things they said.*

Full output: the run's `journal.jsonl` under
`~/.claude/projects/.../subagents/workflows/wf_ba23e852-da4/`, one line per
agent. Re-run with edited post-processing (agents replay from cache):

```bash
Workflow({scriptPath: "<scratchpad>/brain-audit.js", resumeFromRunId: "wf_ba23e852-da4"})
```

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

### Next two, both specified and neither built

Ranked #2 and #3 by the audit. Both come with an implementation design in the
journal; both still need verifying against the code before you write anything.

**The correction guard is a no-op inside `user/*`.** `reconsolidate._dimension_of`
returns `key.partition("/")[0]` — the **subject**, not the dimension — and the
guard compares only that prefix. Every fact about the person shares `user`: 15
of the 21 gold facts in `eval/transcripts.py`, and all four `IDENTITY_KEYS`.
Inside that namespace the guard eliminates nothing and the victim is still
picked by `0.6*activation + 0.4*cosine`, which is exactly the hijack the
docstring says was fixed. Measured on a 6×5 matrix: **16 of 30 cells damage a
fact the correction never names** — "actually I prefer oat milk now" leaves
`user/employer` holding "the user prefers oat milk now". The existing
regression test passes only because its fixture victim is
`coffee_machine/state`, and it asserts `.text()` and never `.key`.

The fix is wiring, not new mechanism: `validate.canonical_key` already exists
and is already the sole gate on the write path. Qualify a candidate only when
it snaps to the same key. Expect one honest regression — the elliptical "no, I
drink semi-skimmed now" stops repairing and lands as a second live milk fact,
which is silent duplication in place of silent corruption of an unrelated
dimension.

**Compiled inferences are spoken as things they said.** Three independent
lenses found this. `recall/answer.py` renders via `Neuron.text()`, which is
`content or gist` and carries no meta, so the `(inferred)` prefix is dropped
on the one path with no model downstream to hedge — and `_about_me` sorts by
`-confidence`, while a compiled contrast carries 0.95 against the extractor's
0.7 default, so derivations sort *first* by construction. On the project's own
365-day corpus, 5 of 11 live facts are derived and "what do you know about me"
speaks three of them flat, including two superseded values recited as current
fact. `person/model.py` carries the guard, `workspace.render` carries it — 
`recall/` is the one renderer that does not. It violates a §17 standing
decision and HANDOFF trap #6 verbatim.

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

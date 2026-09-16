# What to build next

Written 2026-08-23, after a dead-code sweep and a five-angle design pass whose
proposals were each required to name a file, a mechanism and a measurement.
Everything below was measured against a 365- or 730-day synthetic history run
through the real pipeline, or read out of the code. Where a number could not
honestly be obtained from the synthetic corpus, that is said rather than
papered over.

The ordering claim: **the device currently says things that are false, and
the instrument that was supposed to catch that measures nothing.** Fix the
speaking before building anything new.

---

## A. Things the device says that are not true

**A1. A fact is stamped with the night that read it, not the day it was said.**
`supersede._create` writes `created_at=now, valid_at=now` from the night's
clock, and `abstraction.consolidate` discards the citing episodes' dates it
already holds. This is the shared root of the re-supersession churn and the
retrieval blackout below — one bug, three symptoms.

Measured over a demo month plus eight nights twenty days apart: neurons
41 → 32, fact rows 17 → 8, versions of `user/pref_meet` 10 → 2 (the true count;
it was stated twice). Age-tag drift on that fact — what the block says against
when it was actually said — 60.4 days → 0.4. The false contrast claim
disappears. 286 tests still pass.

It is the only defect here still writing new bad state on every run, which is
why it goes first: every other fix is cheaper to validate against a store that
has stopped churning.

**A2. Superseded facts never leave the hot cue index.**
`forgetting.decay_and_tier` iterates only `Kind.EPISODE`, and `Store.save`
derives hot scope from tier alone, so a superseded fact stays `Tier.HOT`
forever. `cues.build` then spends its eight semantic seeds on neurons that
`workspace.compete` is structurally forbidden to select, because
`include_superseded=True` is passed by nothing in the codebase.

Measured on the demo: 3 of 8 seeds dead at night 0, 5 of 8 at night 1, 7 of 8
at night 2 — at which point the live fact is not in the seed set at all and
`why` reports `considered 4, selected 1`. This is the mechanism behind the
blackout where `why "when do I prefer meetings"` returns `selected 0, 0 tok`
while the answer sits in the store as a live hot fact. With the fix the
workspace holds 8 items / 139 tok across eight nights instead of collapsing to
1 item / 21 tok.

**A3. `contrast` reads transaction time where it means valid time.**
`engine.contrast` spans `history[0].created_at - history[-1].created_at` and
`store.history_of` orders by `created_at DESC` with no liveness filter. Both
are the wrong column. A genuine change sixty days apart is written on one
night, so the span is 0, `span < 14.0` holds, and the arc claim **never
fires** — while the churn from A1 makes it fire falsely. Today it is a pure
false-positive generator: it speaks only when the bug feeds it, and never when
the feature is warranted.

**A4. Derived facts are spoken unlabelled, and sort first.**
`workspace.render` labels `meta['derived']` with `(inferred)`. `recall/answer.py`
does not — `_about_me`, `_who` and `_what_about` all emit `n.text()` raw, and
two of them sort by `-confidence`, where a compiled contrast carries 0.95
against the extractor's 0.7 default. So an inference outranks everything the
person actually said, on the one path with no model in it to hedge.

**A5. `_who` and `_about_me` skip the topic filter that exists for them.**
`_on_topic` is applied in `_what_about` and `_when_last` and skipped in the
other two, which sort on confidence alone. At day 210 `who is Priya Raman`
answers *"the coffee machine is making that noise again"* — for all four
planted people — because `coffee_machine/state` carries confidence 1.00.

**A6. The correction guard is coarse, and the reason it had to be is a
two-token bug.** `reconsolidate._dimension_of` returns the subject, and the
docstring justifies that entirely on the elliptical "semi-skimmed" case. But
`rules.is_elliptical` inspects the *last* token: `is_elliptical("semi-skimmed")`
is True and `is_elliptical("semi-skimmed now")` is False. Strip the trailing
adverb and the exact-key branch handles it without ever reaching the guard —
which removes the entire justification for the coarseness that lets a milk
correction damage a location fact.

---

## B. The instruments

**B1. Probes must ask for the value current at each checkpoint.** The
0.50 → 1.00 precision curve is an artefact: `synthetic.py:168` plants every
change at `int(days*0.55)` and the probes ask for the new values, so four of
eight are unanswerable by construction before that day. Verified twice — day
180 scores 0.50 in a 365-day run and 1.00 in a 180-day run, and the curve is
unchanged with downscaling, spreading, base-level decay and inhibition ablated
at once. Replace the probe tuple with a step function per query, and add the
symmetric *anachronism* column: the block asserting the new value before it
was ever stated.

**B2. Focus is micro-averaged, and it hides a total failure.** A twelve-item
probe contributes twelve to the denominator and an empty probe contributes
one. Measured at day 180: micro focus 0.89, macro 0.80, **worst probe 0.00,
min_selected 1**. The failing probe retrieved one item, none on topic, and the
aggregate called it healthy. Report the macro mean, the worst probe, and treat
`min_selected == 0` as a hard failure.

**B3. Keep the timing the harness already computes and throws away.**
`Timing` marks ten stages on every turn and `harness.py:249` drops the object.
This is the only number in the system that actually degrades with age: night
p50 18.9 → 92.3 ms over 365 days as neurons go 192 → 1067, turn p50
2.38 → 5.04 ms, roughly linear in store size. And `result.seconds` is 84%
night, so a 2× turn regression is invisible behind it today.

---

## C. Declared and not enforced

The three known instances of this shape were not three loose ends; a
systematic sweep found **thirteen**. The pattern is a declaration with no
enforcement, or a reader with no writer.

- `scoring.deliver` is the only writer of `meta["delivered"]`, nothing calls
  it, and `pending()` — behind `/new` — skips delivered insights. So `/new`
  re-offers the same insights forever.
- `Conduction.symmetric` marks ASSOC, CONTRADICTS and ANALOGOUS symmetric;
  `store.strengthen` canonically orders only `if Edge(etype) is Edge.ASSOC`.
- `contradiction.resolve` is reachable from nothing while
  `contradiction.pending` runs every night: the device asks which of two
  conflicting claims is right and cannot act on the answer.
- `barge_in` is read nowhere — `audio/loop.py` barges in unconditionally, so
  setting it false silently does nothing, and the interruptibility guarantee
  is stated as a fact in the docs.

The sweep should become a test: for every `BrainConfig` field assert a read
outside `config.py`; for every `meta[...]` key assert both a writer and a
reader; for every name in a package `__all__` assert a caller. It starts green
once these are dispositioned and stays green.

---

## D. New capability, in the order the evidence supports

**D1. Temporal contiguity — the timeline is being deleted.** `Edge.NEXT` is
missing from `STRUCTURAL` (`synapse.py:71`), so nightly downscaling multiplies
every succession edge by rho and prunes it at w < 0.02. From
`NEXT_WEIGHT = 0.35` that is 94 nights to deletion. Measured on the shipped
store: NEXT weight has fallen 0.35 → 0.103 mean, and the fraction of episodes
retaining any NEXT edge is 100% at 0–60 days, 64.4% at 60–120, and **0.0% in
every bucket beyond 120 days**.

One line adds it to the frozenset, and it is the same argument the docstring
already makes for `about`: what happened next is a fact about the record, not
a statistic. This is the mechanism behind "what else was going on that week",
which the premise promises and the device cannot currently serve. Score it
with a lag-CRP; `CONDUCTION[NEXT] = (0.50, 0.35)` already encodes the forward
asymmetry, so a forward-skewed curve is a falsifiable prediction rather than a
hope.

**D2. Facts are born emotionally flat.** `supersede._create` builds every fact
with `salience=0.6, storage_strength=2.0` as literal constants, while the
citing episode ids sit in an argument it already receives. The arousal tag
applied at encoding (`binding.py:129`) is discarded at exactly the moment the
memory becomes durable, so a fact distilled from the worst afternoon of
someone's year and one about the bin schedule get identical forgetting curves.
Measured: 16 of 16 facts carry arousal exactly 0.000, while episodes in the
same store average 0.533. Half-life is 63 days at ss=2.0 and 255 at 3.0, so
the lever is large.

The synthetic corpus **cannot** score this — text arousal gives four distinct
values, sd 0.019 across 1047 episodes. It needs a planted-arousal generator
variant first.

**D3. Prospective memory: an intention buried by its own topic.** Ask the
365-day store what it should be doing about the Halloway report and it does
not mention the open, 280-day-overdue commitment. Traced: the loop is
reachable, but entity #7 has fan 236, so fan damping is 0.155 and the loop
arrives at 0.058 against theta=0.05 — then loses all twelve workspace slots,
four of them to near-identical paraphrases. It fails *harder the more you talk
about the topic*, which is the opposite of the intent.

**D4. The schema layer is empty.** `brain.md` §8.1 Stage 4 defines a three-way
encoding route on schemas. After 365 simulated days the store contains **zero
schema neurons** — `induce_schema` requires `min_members=4` and the largest
stem group has 2. It runs once, after consolidation, and nothing consults a
schema when a fact is created. A declared mechanism with zero instances after
a simulated year.

---

## E. What breaks past two years

- **The journal is 70% of the store**, and the storage-pressure ladder cannot
  free a byte of it. The published 8.9 MB is mostly journal, not memories.
- **`cp brain.db` is not a backup.** Under WAL the copy opens as
  `no such table: neurons`. The claim appears in the schema's first comment
  and two design documents; the failure mode is a user restoring what they
  believe is their memory and getting an empty file.
- **There is no schema migration path.** Six features have added columns; the
  seventh bricks any existing store. Right now exactly one real store exists,
  which is when this is cheapest to fix.
- **Installing the real embedder collapses the two-tier vector index** for the
  life of the process — the second trap in a swap described everywhere as a
  one-line change.
- **The open segment loses ten of twelve turns** when a conversation stays on
  one subject, because only a sealed episode is written.

---

## Not doing

- **Deleting the inert mechanisms.** `workspace_tokens` and `salience_floor`
  are measured inert but wired and executed. That is a decision about whether
  a mechanism earns its complexity, not a cleanup, and it is the author's.
- **Deleting the defensive guards** a dead-code sweep calls unreachable — the
  `if total <= 0` divisions, the stale-heap check, the `else 0.0` before a
  `pstdev` that raises on one sample. Unreachable given today's callers is not
  unreachable, and each is one line between a future wrong call site and a
  crash on a device with no screen.
- **Pattern separation at encoding**, for now. The redundancy is real — four
  of twelve workspace slots in the Halloway probe went to restatements — but
  the 97.8% near-duplicate rate is an artefact of an 82-string template
  generator, and deciding this on that number would be deciding it on fiction.
  Measure it on `eval/transcripts.py` first.

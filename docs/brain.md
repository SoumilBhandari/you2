# The Brain

*The complete software design for You2's memory system. Last revised 8 August 2026.*

[docs/design.md](design.md) says the memory is the product and then, honestly, stops — it names three stores, says
"consolidate, don't accumulate," and admits that consolidation, contradiction resolution and forgetting are unsolved.
This document is the part that was missing. It specifies a working brain: what a memory *is*, how thinking is made to
cost something bounded, what makes an experience worth keeping, what happens at night, and how anything is ever
forgotten.

It is opinionated on purpose. Every mechanism here is either drawn from a specific result in memory research or is a
deliberate departure that says why.

---

## Contents

1. [The thesis](#1-the-thesis)
2. [The substrate — neurons and synapses](#2-the-substrate--neurons-and-synapses)
3. [Activation — how it thinks](#3-activation--how-it-thinks)
4. [The workspace — why it doesn't read everything](#4-the-workspace--why-it-doesnt-read-everything)
5. [Perception — listening](#5-perception--listening)
6. [Salience — the encoding gate](#6-salience--the-encoding-gate)
7. [Encoding — binding an experience](#7-encoding--binding-an-experience)
8. [Sleep — the night cycle](#8-sleep--the-night-cycle)
9. [Forgetting — the part nobody ships](#9-forgetting--the-part-nobody-ships)
10. [Discovery — the connections engine](#10-discovery--the-connections-engine)
11. [Recall — the hot path](#11-recall--the-hot-path)
12. [The numbers](#12-the-numbers)
13. [Software layout](#13-software-layout)
14. [Evaluation](#14-evaluation)
15. [Build order](#15-build-order)
16. [Standing decisions](#16-standing-decisions)
17. [Open questions](#17-open-questions)

---

## 1. The thesis

**The brain metaphor is not decoration. It is the specification.**

Most "AI with memory" systems are a vector store with a cron job, and they fail the same way: they accumulate, so
retrieval precision falls as the corpus grows, and by month three the system is worse than no memory at all. The
design doc already names this. What it doesn't give is a mechanism, and a mechanism is the only thing that fixes it.

So take the metaphor literally. Four commitments follow, and everything else in this document is downstream of them.

### 1.1 Retrieval is spreading activation, not similarity search

You do not scan your memory when someone says a sentence. A few things light up, and they light up the things
attached to them, and that dies out after two or three hops. The cost of remembering is proportional to how much lit
up — never to how much you know.

This is the answer to *"it shouldn't just read everything."* Not a heuristic on top of a scan: a retrieval mechanism
whose cost is structurally independent of corpus size. Embeddings get used exactly once, to pick the handful of
starting points. Everything after that is graph traversal with a decay term and a firing threshold, and it terminates
because activation runs out.

A system built this way *cannot* read everything. That's the property we want.

### 1.2 The context window is the global workspace

Global workspace theory says consciousness is a limited-capacity broadcast: many unconscious processes compete, one
coalition wins, and the winner is broadcast to everything else. That is a startlingly exact description of what a
token budget does to retrieved memory.

So the 400-token memory block from [design.md §5.6](design.md#56-the-rule-that-protects-the-hot-path) stops being an
arbitrary latency cap and becomes the workspace capacity. Selection stops being "top-k by score" and becomes a
competition with lateral inhibition — which is what stops the block filling with eight paraphrases of the same fact.

### 1.3 Salience is prediction error

The usual approach is to ask a model "how important is this?" That produces flattery: everything a person says
sounds important out of context.

Real memory doesn't work that way. Encoding strength tracks **surprise** — the gap between what your model of the
world predicted and what actually happened. Prediction errors are what disrupt hippocampal representations and drive
memory updating; high-error events are encoded more strongly *and* act as event boundaries.

That is computable here, and cheaply. Before encoding an utterance, we have already run retrieval on it for the hot
path. If the retrieved memories predict what was just said, surprise is low — we knew that. If the utterance is
orthogonal to everything retrieved, or contradicts it, surprise is high. The retrieval we needed anyway *is* the
predictor. Salience costs one dot product.

### 1.4 Sleep is where structure comes from, and downscaling is the mechanism

Waking experience is net synaptic potentiation: everything you attend to strengthens something. Left alone that
saturates — runaway potentiation, falling signal-to-noise, and learning stops working. The synaptic homeostasis
hypothesis says slow-wave sleep renormalises: synapses are downscaled globally, weak ones fall below threshold and
are lost, strong ones survive proportionally. Signal-to-noise *improves overnight*.

That is precisely the failure mode the design doc identifies and precisely the fix. Nightly multiplicative
downscaling with a prune threshold is why this memory system gets **sharper** with age instead of noisier, and it is
about forty lines of code. It is the highest-leverage thing in the entire project.

---

## 2. The substrate — neurons and synapses

### 2.1 One node type

Every unit of memory is a **neuron**. Not "an episode table, a facts table and a graph" — one table, one set of
dynamics, a `kind` discriminator. This matters: uniform dynamics are the whole reason activation can flow from a
remembered Tuesday to a person to a project to a preference without three impedance mismatches in the way.

| Kind | What it is | Created by | Decays? |
|---|---|---|---|
| `episode` | Something that happened. A conversation turn or a bounded exchange. | Encoding, in the moment | Yes, fastest |
| `fact` | A durable claim with identity, provenance, and a validity window | Consolidation | Slowly |
| `entity` | A person, project, place, organisation, artefact | Extraction | Barely |
| `schema` | An induced abstraction — a pattern over many episodes | Consolidation | Barely |
| `insight` | A discovered connection, with its supporting evidence | Discovery | Yes, unless confirmed |
| `loop` | An open commitment, question, or intention | Extraction | Closes rather than decays |
| `affect` | A recorded emotional/arousal state bound to a moment | Perception | Yes |

Common fields on all of them:

```
id, kind, content, embedding, created_at,
storage_strength, retrieval_strength, last_access, access_count,
salience, surprise, arousal, valence,
valid_at, invalid_at, superseded_by,
tier, absorbed, source_ref, confidence
```

`valid_at` / `invalid_at` is Graphiti's bitemporal idea, kept for the reason the design doc gives — it is a mechanism
rather than a policy, and it is the best mechanism anyone has. `tier` is the archival level (§9.4). `absorbed` is the
flag that makes deletion safe (§9.3).

### 2.2 Synapses

Edges are typed and weighted, and the weight is plastic.

| Type | Directed | Learned | Meaning |
|---|---|---|---|
| `assoc` | no | Hebbian | These co-occurred. The bulk of the graph. |
| `about` | yes | structural | Episode → entity mentioned in it |
| `part_of` | yes | extracted | Entity → containing entity |
| `causes` | yes | extracted | Asserted causation |
| `supersedes` | yes | consolidation | Newer fact replaces older |
| `contradicts` | no | discovery | Two claims cannot both hold |
| `next` | yes | structural | Temporal succession within a session |
| `evokes` | yes | Hebbian | Asymmetric — retrieval of A tends to bring B |
| `analogous` | no | discovery | Structural correspondence, not similarity |
| `supports` | yes | discovery | Evidence → insight |

**Hebbian update.** Neurons that fire together wire together — but with a ceiling, or every edge saturates to 1.0 and
the graph carries no information:

```
Δw = η · a_i · a_j · (1 − w_ij)
```

The `(1 − w)` term is soft-bounding. `η ≈ 0.08` awake. Applied to every pair that was co-active in a workspace
broadcast, which is a small set by construction — the workspace holds maybe eight items, so a broadcast creates at
most 28 pairs.

**Fan normalisation.** ACT-R's fan effect: the more things a cue is attached to, the less activation any one of them
receives. Without this, "work" and "coffee" become hubs that dominate every retrieval. Outgoing weight is normalised
by fan-out at spread time rather than stored normalised, so learning stays local:

```
S_ij = w_ij · (1 + ln(1 + fan_i))⁻¹
```

**The graph lives in RAM.** Persisted in SQLite, but held as CSR arrays (`indptr`, `indices`, `weights`, `types`) and
rebuilt at boot. At ten years of use that's about 2.5 M edges — roughly 22 MB as int32/float32/int8. Spreading
activation on that is sub-millisecond. Doing it as SQL queries per hop would cost 10–30 ms on a Pi and blow a
retrieval budget that has to fit inside 50.

### 2.3 Storage

One SQLite file. `copy the file` is the whole backup story, and that is worth a great deal — the design doc is right
about this. WAL mode, one writer, `synchronous=NORMAL`.

Vectors are **int8-quantised with a per-vector scale**. At 384 dimensions, 165 k neurons is 63 MB quantised against
253 MB as float32, and a 2 GB Pi is also running the audio stack. Recall loss from int8 on MiniLM embeddings is
negligible at the precision this needs, and the vector index is only picking cue seeds anyway — the graph does the
real work.

**Two-tier search.** Brute-force cosine over everything is 20–60 ms on an A76, which doesn't fit. So:

- **Hot tier** — everything from the last 90 days, plus all facts, entities, schemas and open loops. 5–15 k vectors.
  Brute force in under 5 ms. This is what the hot path searches.
- **Cold tier** — archived episodes. Searched only on an explicit deep-recall request, or at night.

That split is not a compromise made under protest. Recent-plus-consolidated being the fast path *is* how human recall
behaves, and pushing raw old episodes out of the fast path is what keeps precision up.

---

## 3. Activation — how it thinks

Three terms, composed. This is the ACT-R activation equation with a graph traversal in the middle and a
two-component strength model underneath.

### 3.1 Base-level activation — recency and frequency

```
B_i = ln( Σ_k (t − t_k)^(−d) )
```

Power-law decay over the history of accesses, `d ≈ 0.5`. Computing the full sum over every past access is expensive
and unnecessary; the standard approximation keeps a decayed accumulator and updates it on access, which is O(1) and
within a few percent.

**The two-component refinement.** Bjork's New Theory of Disuse splits memory strength in two, and it's the right
model for a system that lives with someone for years:

- **Retrieval strength** — how accessible this is *now*. Decays with time, rises on access.
- **Storage strength** — how deeply learned. **Never decreases.** It governs how slowly retrieval strength decays.

And the useful bit: storage strength grows most when retrieval was *hard*. Recovering something you hadn't touched in
eight months should make it much more durable than re-reading something you saw yesterday.

```
retrieval_strength ← 1.0
storage_strength   ← storage_strength + α · (1 − retrieval_strength_before)
decay_rate_i        = d₀ / (1 + storage_strength_i)
```

This gives forgetting a principled shape — the thing the design doc says no framework has. Memories that get used
survive; memories that don't fade at a rate set by how well they were learned; and nothing that gets recalled ever
gets *weaker* for having been recalled.

### 3.2 Spreading activation — the traversal

Best-first, bounded, with a threshold. This is the core loop of the whole system.

```
seed:    cue nodes, activation = cue weight
repeat:  pop highest-activation unexpanded node i
         if a_i < θ: stop                       # firing threshold
         if visited ≥ budget: stop              # hard cap
         for each neighbour j of i:
             Δ = a_i · S_ij · γ^(hop)           # γ ≈ 0.45 per hop
             if Δ < θ_edge: skip                # don't even push it
             a_j += Δ
             push j
```

Four things make it terminate fast and stay relevant:

**Decay per hop** (`γ ≈ 0.45`). Three hops is `0.09` of the source activation. In practice nothing survives past hop 3
unless it was reached by several paths at once — which is exactly the convergent evidence you *do* want.

**Firing threshold** (`θ ≈ 0.05`). Below it a node doesn't fire and doesn't propagate.

**Fan normalisation** (§2.2). Hubs spread thin.

**Refractory period.** A node fires once per cycle. Prevents ping-ponging between two strongly-coupled nodes.

The result: a query typically visits 200–800 neurons out of however many exist. **Retrieval cost is set by the shape
of the activation cascade, not by the size of the store.** That property is the entire point, and it is worth
protecting against every future optimisation that would quietly reintroduce a full scan.

### 3.3 Cues — where the spark comes from

The cue set is what turns an utterance into starting points, and getting it right matters more than any parameter
above. Five sources, weighted:

| Source | Weight | What it catches |
|---|---|---|
| Semantic — kNN on the utterance embedding, hot tier | 1.0 | Topical entry points |
| Entity mentions — exact and alias match | 1.2 | Named people, projects. Highest-precision cue available |
| Conversational context — what's already in the workspace | 0.8 | Pronouns, follow-ups, "that thing" |
| Temporal — "last week", "in March", recency prior | 0.6 | Explicitly time-scoped recall |
| Ambient — time of day, day of week, session gap | 0.2 | Weak priming, e.g. Monday-morning context |

Entity match outranking embedding kNN is deliberate. If someone says a name, that is a far stronger signal than any
cosine distance, and treating it as one avoids the failure where "Anderson" retrieves five semantically-adjacent
conversations about *other* people.

---

## 4. The workspace — why it doesn't read everything

Activation produces a few hundred lit neurons. The prompt has room for maybe eight. Something has to choose, and
choosing badly is how memory-augmented systems end up worse than no memory.

### 4.1 Lateral inhibition

Winners suppress their neighbours. Concretely: after selecting a neuron for the workspace, every neuron within
cosine similarity `> 0.8` of it has its activation multiplied by `(1 − λ)`, `λ ≈ 0.7`.

This is maximal-marginal-relevance wearing a lab coat, and the framing earns its keep because it says *why*: without
it the workspace fills with eight restatements of one memory, which reads to the user as the device having exactly
one thing to say about them.

### 4.2 The competition

Each candidate has an activation `a`, a token cost `c`, and a utility multiplier `u` by kind:

| Kind | `u` | Why |
|---|---|---|
| `fact` | 1.4 | Densest signal per token, and current by construction |
| `loop` | 1.3 | Open commitments are almost always worth surfacing |
| `entity` | 1.1 | Cheap, and grounds the rest |
| `schema` | 1.0 | |
| `episode` | 0.8 | Verbose, and usually already absorbed into a fact |
| `insight` | 0.7 | Push sparingly — see §10.4 |

Selection is greedy by `a · u / c` under the token budget, with inhibition applied after each pick. Greedy is within a
few percent of optimal for knapsack at this size and costs microseconds.

Two invariants, both enforced in the retriever rather than hoped for:

- **The block never exceeds its budget.** Default 400 tokens. This is a hard cap; the retriever truncates.
- **The block is ordered by kind, not by score** — facts, then loops, then entities, then episodes. Stable ordering
  keeps the prompt prefix cacheable for longer.

### 4.3 Broadcast, and what it teaches

Winning the workspace has consequences, which is what makes this a learning system rather than a query planner:

1. Every winner's retrieval strength is reset to 1.0 and its storage strength increased by the desirable-difficulty
   rule (§3.1).
2. Every pair of co-active winners gets a Hebbian bump on their `assoc` edge, creating it if absent.
3. The workspace contents persist as context cues for the next turn (§3.3), which is how follow-up questions work
   without re-deriving everything.

Point 2 is where the graph actually comes from. Nobody writes those edges by hand and no extraction pass produces
them. **The association structure is a by-product of use** — things that get remembered together become linked, and
the link is what makes them get remembered together next time. That is the loop that makes the thing feel like it
knows you, and it costs one UPDATE per pair.

---

## 5. Perception — listening

The design doc treats ASR output as the input to memory. It isn't. **How something was said carries encoding
signal that the transcript throws away**, and the microphone array is already producing it.

### 5.1 Prosody is free arousal

From audio frames the pipeline already has, at negligible cost:

| Feature | Computed from | Signals |
|---|---|---|
| RMS energy, mean and variance | frame amplitudes | emphasis, agitation |
| F0 mean and variance | autocorrelation, 30 ms windows | arousal, question intonation |
| Speech rate | syllable-nucleus rate | urgency, excitement |
| Pause structure | VAD gaps within a turn | hesitation, word-finding, weight |
| Onset latency | gap since the device stopped speaking | engagement |

These fold into a `(valence, arousal)` estimate per turn. Arousal is a direct multiplier on encoding strength (§6),
which is emotional modulation of consolidation — the mechanism by which the things you felt strongly about are the
things you still remember. **A memory device with a microphone that ignores prosody is throwing away the single
cheapest salience signal available to it.**

Two honest caveats. Prosodic valence is much less reliable than arousal — arousal is largely energy and pitch
variance and is robust; valence from acoustics alone is weak, so text carries most of the valence estimate. And this
is speaker-relative: it needs a running per-speaker baseline before the numbers mean anything, so the first week is
calibration.

### 5.2 The attention state machine

"Wake-word activated, never always-recording" is a standing decision and it stays. But a single wake word per
utterance is not how attention works, and it makes the device feel deaf.

```
DORMANT ──wake word──> ATTENDING ──turn end──> RESPONDING
   ^                       ^  |                     |
   |                       |  └──dwell expiry───────┤
   └───────dwell expiry────┴────follow-up detected──┘
```

**Dwell.** After a response ends, stay in `ATTENDING` for ~8 seconds. A follow-up in that window needs no wake word.
This is sustained attention, it's how conversation actually works, and it is the difference between a device you talk
to and a device you issue commands at.

**Orienting.** The wake word is an orienting response, and it does more than gate the microphone: it snapshots
ambient context (time, gap since last session, time of day) into the cue set before a single word is transcribed.

**Binding.** Pronouns and deixis — "that", "it", "the thing we talked about" — resolve against the *workspace*, not
against the transcript. That's the payoff for keeping the workspace as a persistent object across turns.

Raw audio is still processed and discarded, and nothing here is recorded outside an active session. Prosodic
features are seven floats per turn; they are not audio, and they cannot be reconstructed into audio.

### 5.3 Reading, not just listening

Conversations first, per the design doc — high-signal precisely because the user chose to say them out loud. A
watched markdown folder second. Both go through the identical salience gate; a document just produces many candidate
episodes at once instead of one.

**Documents get a salience discount** (`×0.6`). Something you wrote in a file is weaker evidence about you than
something you said out loud, and without the discount a single imported vault swamps two years of conversation.

---

## 6. Salience — the encoding gate

Six terms. Five of them are computed without an LLM call; the sixth is an existing call's by-product.

```
salience = 0.30·surprise
         + 0.20·arousal
         + 0.15·self_reference
         + 0.15·goal_relevance
         + 0.10·novelty
         + 0.10·explicit
```

**Surprise** — `1 − max cosine(utterance, retrieved_workspace)`, sharpened. The workspace is what memory predicted
would be relevant. If the utterance lands inside it, we already knew this. If it lands outside, something new
happened. Free: the retrieval already ran.

**Arousal** — §5.1, normalised against the speaker's running baseline.

**Self-reference** — first-person constructions, preference statements, commitments, evaluations. The
self-referential encoding effect is one of the largest and most replicated effects in memory research; a lexical
scorer plus a small pattern list captures most of it for zero cost.

**Goal relevance** — does this touch an open `loop` or a currently-active project? Computed from the activation
cascade, which already visited them.

**Novelty** — `1 − max cosine(utterance, entire hot tier)`. Distinct from surprise: surprise is "memory failed to
predict this in context," novelty is "nothing like this exists at all."

**Explicit** — "remember that", "don't forget", "note this". Pinned to 1.0, and pinned memories never decay below a
floor. When someone tells you to remember something, you remember it.

### 6.1 Three outcomes, not two

| Salience | Outcome |
|---|---|
| `< 0.15` | **Discard.** Greetings, acknowledgements, "thanks". Never written. |
| `0.15 – 0.55` | **Encode weak.** Episodic only, fast decay, low replay priority. |
| `> 0.55` | **Encode strong.** Episodic, flagged for priority replay tonight, arousal-tagged. |

Note what this is *not*: a decision about what to keep permanently. Per the design doc, capture stays permissive —
almost everything above the floor is written. **Salience is a replay priority, not a filter.** That is how the
hippocampus does it: everything gets encoded, and what got tagged is what gets replayed and therefore what survives
into long-term structure. Deciding at capture time, when you have the least context about what matters, is the
mistake.

---

## 7. Encoding — binding an experience

Everything in this section runs **after the spoken reply**. Never in the critical path. This is a standing decision
and the hot path must not acquire a single new blocking call.

1. **Segment.** An episode is a bounded exchange, not a turn. Boundaries are drawn at topic shifts, long pauses, and
   — following the event-boundary literature — at **high prediction error**. A surprising statement ends the previous
   episode and starts a new one, which is why surprising things are remembered as their own moment rather than
   blurred into what preceded them.
2. **Write the episode neuron** with its salience, arousal, valence and surprise.
3. **Extract entities** — the only LLM call in the write path, batched per episode, and skipped entirely for
   weak-salience episodes. Resolve against existing entities by alias and embedding; create if genuinely new.
4. **Bind.** `about` edges episode → entities, a `next` edge to the previous episode, Hebbian bumps between
   co-mentioned entities.
5. **Detect loops.** Commitments and intentions ("I'll look into…", "I need to…") become `loop` neurons with an
   expected-by estimate.
6. **Enqueue for tonight.** Priority = salience.

Facts are deliberately **not** extracted here. Fact extraction requires knowing what the fact supersedes, and that
requires context the moment doesn't have. It happens at night, over a batch, where it belongs.

---

## 8. Sleep — the night cycle

Eight idle hours on a mains-powered machine. This is the part of the design that turns the project's worst constraint
into its best asset, and it is structured as real sleep is structured, because each stage does a different job.

The night runs cycles, not one pass: **NREM → REM**, repeated, with NREM dominant early and REM dominant late. That
ordering is not aesthetic — consolidation has to happen before recombination has anything worth recombining.

### 8.1 NREM — replay and consolidation

**Stage 1 · Prioritised replay.** Sample episodes for replay with probability

```
P(i) ∝ (salience_i · surprise_i · recency_i) / (1 + times_replayed_i)^β
```

This is prioritised experience replay, which is itself derived from the observation that hippocampal replay is
prioritised rather than uniform. The `times_replayed` denominator is what stops the same dramatic Tuesday being
re-consolidated every night for a year.

**Stage 2 · Ripple batching.** Replay is compressed and clustered, not sequential. Group sampled episodes into
batches of 5–15 that share entities, and process each batch as **one** LLM call. This is both biologically right and
operationally decisive: it's roughly 10× cheaper than per-episode calls, and the extraction is *better*, because a
fact that spans several conversations is only visible when they're read together.

**Stage 3 · Abstraction.** From each batch: session gist, candidate facts with provenance, new entities, new edges.
Facts arrive with a **stable key** (`subject/predicate` — `user/coffee_preference`) which is what makes supersession
possible at all.

**Stage 4 · Schema assimilation.** Does the candidate fit an existing schema?

- *Consistent* → fast-track. Strengthen the schema, low-cost integration. (Schema-consistent memories consolidate
  faster; this is a real effect and it's also the cheap path, which is convenient.)
- *Novel* → create the fact, attach to the nearest schema.
- *Conflicting* → **accommodation**. Do not silently overwrite. Route to Stage 5.

**Stage 5 · Supersession and contradiction.** For each candidate sharing a key with an existing fact:

| Relationship | Action |
|---|---|
| Restatement | Bump confidence, update `last_seen`. No new record. |
| Refinement | New fact, `supersedes` edge, old one gets `invalid_at = now`. |
| Reversal | Same, plus keep the old one queryable — "you used to…" is valuable. |
| Genuine conflict, unresolvable | `contradicts` edge, both stay valid, **generate a question**. |

That last row is the good one. An unresolvable contradiction becomes something to ask: *"In March you said the
Anderson deadline was firm; on Tuesday you said it had moved. Which is current?"* One question resolves a fact that
would otherwise poison retrieval indefinitely, and asking is honest in a way that silently picking the newer record
is not.

**Stage 6 · Synaptic downscaling.** Global, multiplicative, then prune:

```
w ← w · ρ            for all edges, ρ ≈ 0.97
delete where w < w_min                       (w_min ≈ 0.02)
```

Multiplicative is the essential detail. Subtractive decay destroys weak-but-real structure and leaves strong-but-noisy
structure intact. Multiplicative preserves *relative* structure exactly while pushing the accumulated noise floor
below threshold — signal-to-noise improves, which is the whole claim of the synaptic homeostasis hypothesis and the
reason retrieval gets sharper rather than noisier with age.

Roughly forty lines. It is the single most valuable component in this document.

**Stage 7 · Decay and tiering.** Retrieval strength decays per §3.1. Episodes marked `absorbed` and below threshold
demote a tier (§9.4).

### 8.2 REM — discovery

Runs after at least one full NREM pass, on the freshly consolidated structure.

**Stage 8 · Remote association.** Sample neuron pairs that are *far apart* in the graph — 3+ hops, low direct
association — but share structural neighbourhood signatures. Weight sampling toward nodes with high recent activation,
so the device dreams about what's currently on your mind rather than uniformly about everything.

**Stage 9 · Structure mapping.** For each candidate pair, compare **relational shape** rather than content. Extract
the local subgraph around each node as typed relation tuples and look for alignment: same relation types, same
arity, systematically corresponding roles, different entities.

This is the mechanism the design doc calls the prize and a research problem. It stays hard, but it's hard for a
*specific* reason with a *specific* answer: embeddings measure surface similarity, and analogy is structural
correspondence between things whose surfaces differ. SME ignores surface features precisely so it can find mappings
between distant domains that share representational structure. Cosine distance cannot do this in principle — not
badly, *in principle* — which is why every "here's a connection!" feature built on embeddings surfaces topical noise.

Cheap alignment first (relation-type multiset overlap, degree sequence, path signature); only candidates that survive
that get an LLM call to state the mapping and be scored.

**Stage 10 · Counterfactual probing.** Occasionally, generate a plausible-but-false variant of a well-supported fact
and check whether retrieval rejects it. Failures indicate weakly-grounded regions and raise their replay priority.
This is a schema-robustness test, it is cheap, and it is the closest thing here to actual dreaming.

**Stage 11 · Insight scoring.** Every candidate is scored on four axes and must clear all of them (§10.4). Most die
here, and that is the point.

### 8.3 Waking — the morning artefacts

By 06:00 the night has produced:

- **The profile block** — a stable, cache-friendly summary of durable facts, rebuilt nightly rather than per-query.
  Per [design.md §8](design.md#making-the-local-build-tolerable) this is the highest-leverage latency mitigation
  available: a stable prefix keeps the KV cache warm, so query-specific retrieval only has to add a small addendum
  *after* the cache breakpoint.
- **Pending questions** — contradictions worth resolving, and stale open loops.
- **Surfaced insights** — the few that cleared scoring, held for pull rather than pushed.
- **The dream journal** — a plain-text log of what was replayed, learned, superseded and pruned. Auditable, and the
  first thing you read when retrieval quality moves.

### 8.4 Interruptibility

The night job yields on wake-word detection within 50 ms and checkpoints per batch. Someone walking in at 3 a.m. and
saying "Friday" must never wait for a consolidation batch to finish. It also throttles on temperature — a
thermally-throttled Pi at 3 a.m. costs nothing, but it must not still be throttled at 9 a.m.

---

## 9. Forgetting — the part nobody ships

The design doc says no framework has a principled decay model. Here is one.

### 9.1 Forgetting is loss of access, not loss of data

Retrieval strength decays. Storage strength never does. A memory becomes unretrievable long before it becomes
unrecoverable, and an explicit deep-recall query can still reach into the cold tier and find it. This mirrors what
actually happens to people, and it means "forgetting" is never data loss — which matters a great deal for a device
whose entire pitch is that it holds your life.

### 9.2 Four ways to lose something, ranked by how safe they are

| Mechanism | Reversible | What it removes |
|---|---|---|
| Retrieval-strength decay | Yes — one access restores it | Nothing. Access only. |
| Synaptic downscaling | Partly | Weak *edges*. Neurons untouched. |
| Tier demotion | Yes | Fast-path availability |
| Absorbed-episode compaction | No | Verbatim text of episodes proven redundant |

Only the last is destructive, and it is heavily gated.

### 9.3 Absorption gating

An episode can only be compacted when **every** claim it carries is provably represented elsewhere: it is marked
`absorbed` only if each extracted fact from it exists as a `fact` neuron whose provenance cites this episode. The
episode's gist survives; the verbatim text goes.

The rule: **never delete unabsorbed material.** If consolidation hasn't understood it yet, it stays. This is what
makes aggressive compaction safe, and it's checkable — a test asserts that no compacted episode has an unabsorbed
claim.

### 9.4 Tiers

| Tier | Contents | Searched |
|---|---|---|
| `hot` | ≤ 90 days, all facts/entities/schemas/loops | Every query |
| `warm` | 90 days – 1 year, or absorbed | Deep recall, and nightly |
| `cold` | > 1 year and absorbed. Gist + embedding only. | Explicit deep recall |
| `tomb` | User-deleted | Never — and the content is gone |

`tomb` is the only unrecoverable state and it is reachable only by explicit user action. "Forget that" must actually
work, immediately and completely, including the facts derived from it — provenance makes that traceable, which is a
second reason to carry it.

### 9.5 What never decays

Pinned memories (`explicit = 1.0`), identity facts about the user, open loops until closed, and anything accessed in
the last 30 days. Small list, enforced in one predicate, prevents the entire class of "it forgot my name" failures.

---

## 10. Discovery — the connections engine

[design.md §6](design.md#6-the-connections-layer) has this right and the ranking stands: contradiction, recurrence
and open loops are tractable now; bridging is the prize and a research problem. What follows is mechanism for each.

### 10.1 Contradiction

Falls out of consolidation for free (§8.1 stage 5). Two facts sharing a key with incompatible values, neither
clearly superseding the other. **May push**, because a false positive is cheap and a true positive is valuable.

### 10.2 Recurrence

Pattern detection over the episode log. Not "you mentioned X a lot" — that's a word count. The useful form is
**conditional**: an entity or affect state that co-occurs with a context far more than base rate.

```
lift = P(affect=drained | context=tuesday_meeting) / P(affect=drained)
```

Report when `lift > 2.0`, `n ≥ 4`, and the pattern spans at least three weeks. The time-span requirement kills the
"noticed a pattern on Wednesday, it was one bad week" failure. **May push.**

### 10.3 Open loops

A `loop` neuron with no closing evidence past its expected-by date. Genuinely underrated, and the easiest real value
in this document — a device that says *"three weeks ago you said you'd look into the Anderson contract; it hasn't
come up since"* is doing something no phone does. **May push, at most one per day.**

### 10.4 Bridging

§8.2. **Pull only — may never push.** A wrong analogy is embarrassing in a way a wrong contradiction is not; it makes
the whole system feel stupid, and that impression doesn't wash out.

### 10.5 Insight scoring

Four axes, all required, each on `[0,1]`:

| Axis | Test | Threshold |
|---|---|---|
| **Non-obvious** | Graph distance ≥ 3 and low prior co-activation | > 0.5 |
| **Grounded** | Cites ≥ 2 real neurons, both above a confidence floor | 1.0 — hard requirement |
| **Actionable** | Touches an open loop, an active project, or a decision | > 0.4 |
| **Novel** | Not within cosine 0.85 of a previously surfaced insight | > 0.6 |

Grounding is binary and non-negotiable. An insight that can't cite two memories is a hallucination with a citation
field, and shipping one of those costs more trust than ten good insights buy.

### 10.6 The rating loop, from day one

Everything surfaced is logged with its type, its score on all four axes, and the user's one-keypress verdict. After
three months that's a precision figure *per connection type*, which tells you exactly which categories to cut. The
design doc is right that there is no benchmark for "does this feel like it knows me after two years" — so build the
instrument that produces one, on the first day, before there's anything to measure.

---

## 11. Recall — the hot path

The budget is unforgiving: retrieval has ~50 ms inside a 620–780 ms round trip.

| Step | Cost | Notes |
|---|---|---|
| Cue extraction | 3–8 ms | Embedding is the cost; entity match is a hash lookup |
| Hot-tier kNN | 2–5 ms | int8 brute force over 5–15 k |
| Spreading activation | 1–3 ms | In-RAM CSR. 200–800 nodes visited |
| Inhibition + workspace | < 1 ms | Greedy over ~50 candidates |
| Block assembly | 1–2 ms | Templated, token-counted |
| **Total** | **8–19 ms** | Comfortably inside budget |

Two paths bypass the LLM entirely, both of which keep working with no network:

**Intent router** — timers, time, device state. ~200 ms, no model.

**Memory-answerable questions** — "what did I say about X", "when did I last…", "what am I working on". These are
*resolvable from the workspace alone* and templated into a spoken answer. This is a bigger fraction of real usage
than it looks, it's the best possible offline story, and it's the fastest path in the system.

### 11.1 Reconsolidation

Retrieving a memory makes it labile: a retrieved trace is re-encoded in the context of retrieval, and can be modified
in the process. That's a real property of memory and it's exactly the repair path the design doc asks for.

So when a memory is used in a turn and the user's response *corrects* it, the correction attaches immediately — the
fact is superseded then and there, not at 3 a.m. **"No, that's out of date" must work in the moment.** It's the
single most important trust interaction the device has, and deferring it to the night job means the device argues
with you twice about the same thing.

---

## 12. The numbers

Assumptions: 30 exchanges/day, ~8 episodes/day after segmentation, 10 years.

| Quantity | Year 1 | Year 10 |
|---|---|---|
| Episodes | 2,900 | 29,000 |
| Facts | 900 | 6,500 (supersession keeps this sublinear) |
| Entities | 400 | 2,200 |
| Schemas / insights / loops | ~200 | ~1,800 |
| **Neurons** | **~4,400** | **~39,500** |
| Edges before pruning | 60 k | 900 k |
| Edges after nightly downscaling | 45 k | ~380 k |
| DB size | ~40 MB | ~350 MB |
| Hot-tier vectors | ~2,000 | ~4,500 |
| Graph in RAM | 0.5 MB | 3.5 MB |

These are smaller than the earlier §2.3 worst case, and deliberately so: supersession and pruning are what make the
store grow sublinearly. **A memory system whose size is linear in elapsed time has no consolidation.** The growth
curve is the diagnostic.

Nightly cost, year 10: ~150 replay batches × ~1,200 tokens ≈ 180 k tokens. On Groq's free tier that is free; paid,
about $0.02 a night. Wall clock roughly 25 minutes of an eight-hour window.

### 12.1 Tunables

Every constant in this document lives in one `BrainConfig`. No magic numbers scattered through modules — this system
will be tuned for months, and tuning it means editing one file and re-running the eval harness.

```
spread_decay γ 0.45     fire_threshold θ 0.05    visit_budget 800
hebbian_η 0.08          downscale_ρ 0.97         prune_w_min 0.02
base_decay d₀ 0.5       storage_gain α 0.30      inhibition λ 0.70
workspace_tokens 400    salience_floor 0.15      strong_salience 0.55
dwell_seconds 8.0       hot_tier_days 90         replay_β 0.60
```

---

## 13. Software layout

```
software/
  you2/
    config.py        every tunable, one dataclass, TOML-loadable
    clock.py         injectable time — non-negotiable, see below
    substrate/       neurons, synapses, CSR graph, vectors, embeddings, SQLite
    activation/      base-level, spreading, inhibition, workspace, cues
    perception/      prosody, attention state machine, discourse, salience
    encoding/        segmentation, extraction, binding
    consolidation/   night orchestrator, replay, abstraction, supersession,
                     homeostasis, forgetting, dream
    discovery/       contradiction, recurrence, open loops, bridging, scoring
    recall/          retrieve, memory-answerable, reconsolidation
    llm/             OpenAI-compatible streaming client + deterministic fake
    runtime/         hot-path pipeline, intent router, night scheduler, CLI
    eval/            synthetic history generator, harness, metrics
  tests/
```

**`clock.py` is architecture, not a testing convenience.** A system whose core mechanisms are decay, nightly cycles
and multi-year drift cannot be tested in real time. Every timestamp comes from an injected clock, so the eval harness
can run two years of use in ninety seconds. Any module that calls `time.time()` directly is a bug.

**Two escape hatches keep the whole thing runnable with no network and no model downloads:** a deterministic
hash-based embedder and a scripted fake LLM. Both are used by the test suite, which means the test suite is fast,
offline, and deterministic.

---

## 14. Evaluation

The design doc's most uncomfortable observation is that there is no benchmark for the thing that defines the product.
Three instruments, in the order they pay off:

**Synthetic history.** Generate a plausible year — recurring people, evolving projects, changing preferences,
planted contradictions, planted open loops, planted analogies — with **ground truth**, because we generated it. Run
the full pipeline over it at accelerated clock. This is the design doc's "learn in an afternoon what would otherwise
take a year," made concrete.

**Retrieval quality over time.** The metric that matters. Query with held-out probes at simulated month 1, 3, 6, 12,
24 and measure precision@k, superseded-fact leakage, and workspace diversity. **If precision falls with elapsed
time, consolidation is not working**, and that is a single chart that tells you so. It is the primary regression
test of the entire project.

**Ablations.** Turn off downscaling, turn off supersession, turn off inhibition, replace spreading activation with
plain top-k. Each should visibly degrade something. If disabling a mechanism changes nothing, the mechanism isn't
earning its complexity and should be deleted — that check is worth running before every release.

---

## 15. Build order

Strictly bottom-up, because everything above depends on activation being right.

| Phase | What | Gate |
|---|---|---|
| 1 | Substrate: schema, store, CSR graph, vectors, embedder | Round-trips 10 k neurons, graph rebuild < 100 ms |
| 2 | Activation: base-level, spread, inhibition, workspace | Bounded visits; block never exceeds budget |
| 3 | Perception + salience | Gate separates signal from "thanks" on real transcripts |
| 4 | Encoding | Episodes, entities, loops, Hebbian binding |
| 5 | Consolidation | Facts supersede; **downscaling improves precision** |
| 6 | Discovery | Contradiction/recurrence/loops fire on planted ground truth |
| 7 | Recall + runtime + CLI | End-to-end conversation, offline |
| 8 | Eval harness | Two-year curve, ablations |
| 9 | Bridging | Structure mapping; measured, cut if it doesn't clear scoring |

Phases 1–8 need no hardware and no network. That is roughly the whole product.

---

## 16. Standing decisions

Not to relitigate without a specific reason.

**Retrieval is spreading activation.** Any change that makes retrieval cost scale with corpus size is a regression,
however good its benchmark looks.

**One node type, uniform dynamics.** The moment episodes and facts get separate retrieval paths, activation stops
flowing between them and the graph stops being a brain.

**Salience is prediction error, not an LLM's opinion of importance.**

**Downscaling is multiplicative and runs every night.** It is the mechanism that makes this get better with age.

**Nothing unabsorbed is ever deleted.**

**Grounding is binary.** No insight ships without two real citations.

**Bridging may never push.**

**Every write happens after the reply.** The hot path acquires no new blocking calls, ever.

**Time is injected everywhere.**

---

## 17. Open questions

- **Is `γ = 0.45` right?** Two hops feels correct introspectively; three may be where analogy lives. Tunable, and the
  eval harness can answer it — but only once there's a year of synthetic history to answer it against.
- **Does prosodic arousal survive a real room?** A reSpeaker at 2 m with AGC may flatten exactly the dynamic range
  the arousal estimate depends on. Untestable until hardware exists, and it is the least certain claim in §5.
- **Does structure mapping find anything real at personal scale?** SME works on rich propositional representations.
  Conversation-derived graphs are sparser and noisier, and it's genuinely possible that a personal graph never
  contains two subgraphs with alignable structure. Measurable on synthetic history with planted analogies, which is
  the point of planting them.
- **What's the right `ρ`?** Too aggressive and real structure dies; too gentle and the noise floor never drops.
  0.97 nightly is a 33% annual multiplicative decay, which feels right and is a guess.
- **Does the two-tier vector split lose recall that matters?** Suspect the answer is "rarely, and deep recall covers
  it," but that is an assumption, not a measurement.
- **How much does the ripple-batch grouping change extraction quality?** Believed better *and* cheaper, which is the
  kind of claim that deserves an ablation rather than trust.

---

## 18. Corrections log

Kept in the style of [design.md §13](design.md#13-corrections-log) — a correction you can't see is one you'll make
again.

| Believed | Actually |
|---|---|
| Three stores (episodic / semantic / graph) is the right decomposition | Three *views* over one substrate. Separate stores means separate retrieval paths, and activation can't flow between them — which is where the value is. |
| Salience should be judged by the model | Models rate everything as important out of context. Prediction error is computable, free, and closer to what memory actually does. |
| The 400-token cap is a latency compromise | It's the workspace capacity. It should be enforced as a competition with inhibition, not a truncation — and the cap is doing cognitive work, not just budget work. |
| Nightly consolidation means "summarise the day" | Summarising is stage 3 of eleven. The stage that matters is downscaling, and it involves no LLM at all. |
| Connections come from embedding similarity | Similarity finds *related*; the useful connections are structural or temporal. Embeddings cannot find analogy in principle — SME exists precisely because surface features must be ignored. |
| Forgetting means deleting | Forgetting is loss of *access*. Storage strength never decreases. Deletion is a separate, gated, user-facing operation. |

---

*Hardware, enclosure and the voice pipeline: [design.md](design.md). This document is the cold path, in full.*

# The brain

The cold path from [docs/design.md](../docs/design.md), built. Design and
reasoning: **[docs/brain.md](../docs/brain.md)** — read that first.

```bash
cd software
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

.venv/bin/you2 chat --fake          # talk to it, no network, no model download
.venv/bin/you2 why "how's Anderson" # print the whole retrieval
.venv/bin/you2 night                # run consolidation now
.venv/bin/you2 eval --days 365      # simulate a year, measure the curve
.venv/bin/you2 eval --ablate        # check every mechanism earns its place
.venv/bin/pytest                    # 113 tests, ~4 s, offline
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
  recall/          retrieve, memory-answerable answers, reconsolidation
  llm/             OpenAI-compatible client, rule extractor, deterministic fake
  runtime/         hot path, intent router, CLI
  eval/            synthetic history with ground truth, harness, ablations
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
because the corpus was generated. It does **not** measure extraction quality;
how well an 8B model reads a real conversation is a separate question against
real transcripts. Bridging reports as untested rather than as working, because
a rule-based judge genuinely cannot evaluate an analogy and the fake declines
rather than pretending.

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

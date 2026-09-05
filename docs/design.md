# YOU2 — Design Document

*A desk-based voice assistant that runs its own memory and gets to know you.*

Last revised 7 August 2026. This document is self-contained: read only this and you have the whole project.

---

## Contents

1. [What it is](#1-what-it-is)
2. [Why it exists](#2-why-it-exists)
3. [What it is not](#3-what-it-is-not)
4. [Architecture](#4-architecture)
5. [The memory system](#5-the-memory-system)
6. [The connections layer](#6-the-connections-layer)
7. [Hardware](#7-hardware)
8. [Bill of materials](#8-bill-of-materials)
9. [Build sequence](#9-build-sequence)
10. [What can be tested without hardware](#10-what-can-be-tested-without-hardware)
11. [Standing decisions](#11-standing-decisions)
12. [Risks and open questions](#12-risks-and-open-questions)
13. [Corrections log](#13-corrections-log)

---

## 1. What it is

A small object on your desk. You say **"Hey Friday"**, you talk to it, it answers out loud. It has a round display that shows
what it's doing and a light bar that tells you what state it's in.

The part that matters: **it remembers.** Every conversation is captured, distilled overnight into durable facts, and
linked into what it already knows. After a month it knows your projects. After a year it knows things about you that
you've forgotten you said.

The device is mains-powered, always on, and has no battery. It is roughly 134 × 125 × 165 mm and costs around $250–280
in parts.

### On the two names

The project and the object are **you2**. The wake word is **"Hey Friday"**. These are deliberately different things.

*you2* describes what the device is — a second you, an accumulating record — and that's the right name for the thing
and the repo. But a wake word has a different job: it has to be phonetically distinctive, rare enough in ordinary
speech not to false-trigger, and pleasant to say a dozen times a day. "you2" fails the second test badly, being a
homophone for "you too."

The carrier syllable is doing work too. "Friday" alone is rare enough in ordinary speech, but it does appear in
"see you Friday" and "the Friday deadline" — and a device that answers those is worse than one that occasionally
misses. "Hey" gives the detector a run-up and a much cleaner onset to key on, which is the standard reason every
shipped wake word has one.

Nothing in the build depends on either name. The wake word is a custom openWakeWord model you train yourself in about
90 minutes, so it can change at any point without touching a line of pipeline code.

### The thesis in one line

**The model is a download. The memory is the product.**

Anyone can run a good language model. Nobody else has a two-year record of your work, your people, and your
preferences, structured well enough to be useful. That asset compounds, it can't be copied, and it's the only thing in
this project that can't be bought.

---

## 2. Why it exists

### The category is a graveyard, and the reasons are instructive

Every AI hardware device of the last three years died the same way. Humane raised around $230M, shipped the AI Pin,
took some of the worst reviews in consumer tech history, and sold its assets to HP for $116M — devices bricked. Rabbit
shipped the R1 at $199 and the "large action model" turned out to be substantially Android automation scripts.
Limitless went to Meta, Bee to Amazon, both inside eleven months of each other.

Four causes, and You2 is designed against each:

**The phone is right there and does it better.** A general assistant competes with the supercomputer already in your
pocket and loses. → *You2 does not try to be a general assistant. It is worse than a frontier model at trivia and
better than any of them at you.*

**Latency made them feel broken.** Cloud round trips of two to three seconds. People forgave a lot; they did not
forgive waiting. → *Sub-second is the primary spec. Everything else is subordinate to it.*

**Screenless was treated as a religion rather than a design decision.** Humane's projector didn't work in daylight.
→ *You2 has a screen, because output is text and text needs a display.*

**They were general-purpose**, so they competed with everything and were better than nothing.

### The one thing a phone structurally cannot offer

**Apple and Google will never ship local-only.** Their business models don't permit it. A device that promises your
conversations stay in the room is making a claim the incumbents cannot match — and once it holds a year of context, a
better model doesn't replace it.

That's the durable position. Not intelligence — *accumulation*.

---

## 3. What it is not

Explicit non-goals. Each of these has killed a comparable product.

**Not an action-taking agent.** No booking, sending, or controlling things. This is precisely where Rabbit died — the
action layer was the entire promise and it was brittle scripting underneath. Conversation and memory need to be
genuinely good before anything acts on your behalf. This is a v3 problem at the earliest.

**Not always-recording.** Wake-word activated. Other people are in the room and haven't agreed to anything, and several
US states require all-party consent. This is a significant part of why the always-listening pendant companies were
quietly acquired rather than scaled.

**Not a smart-home hub.** Home Assistant exists and is good at that. You2 is a personal-memory companion; the missions
are different and chasing both makes it worse at each.

**No engagement mechanics.** No feed, no streaks, no notifications, no daily active user metric. It speaks when spoken
to. That restraint is a feature and it should survive contact with every future idea.

**Not a wearable.** Earlier exploration of chest-worn and wrist-worn devices concluded that always-on local inference
is watts, not milliwatts. You2 is plugged in. That single constraint is what makes everything else affordable.

---

## 4. Architecture

You2 runs on two clocks, and conflating them is the main way projects like this go wrong.

The **hot path** is conversational. Hard deadline around one second. It is allowed to be dumb.
The **cold path** is memory. It has hours, runs while you sleep, and is where all the interesting work is.

Most people building this spend 90% of their time on the hot path because it's the part you can demo. That's backwards.
The hot path is five open-source components in a row. The cold path is unsolved.

### 4.1 Hot path

```
mic → wake word → VAD → turn detection → ASR → LLM → TTS → speaker
                    ↑                                          │
                    └────────── echo cancellation ─────────────┘
```

| Stage | Component | Measured cost (Cortex-A76 @ 2.4 GHz) |
|---|---|---|
| Wake word | openWakeWord, custom **"Hey Friday"** | <5 ms per 80 ms frame (~6% of one core) |
| VAD | Silero VAD v6 | <1 ms per 30 ms chunk |
| Turn detection | pipecat smart-turn v3 | 60–120 ms on ARM |
| ASR | Moonshine Tiny, streaming | **237 ms** |
| LLM | Groq `llama-3.1-8b-instant` | 120–180 ms TTFT + 40–80 ms home RTT |
| TTS | Piper medium, first sentence | ~165 ms |
| **Total** | | **~620–780 ms** |

### 4.2 The three rules that make the budget close

**Overlap everything.** Run sequentially, these stages sum to roughly 1.9 s and the product fails. Transcription starts
while you're still talking. The model starts on a probably-complete transcript, not a final one. Synthesis starts on
the first complete sentence. Nothing waits for the stage before it to finish.

**Never wait for the LLM.** A 40-token reply generated at 8.8 tok/s is 4.5 seconds. **Sentence-chunk the token stream
and pipe each completed clause into TTS as it lands.** First audio reaches the speaker in ~600 ms while generation
continues underneath. That single decision converts 4.5 s into 600 ms of *perceived* latency, and it is the thing every
"local assistant that feels broken" on the internet skipped.

**Endpointing is the hidden cost.** The largest fixed delay is deciding you've stopped speaking. A naive 700 ms silence
timeout burns most of the budget on every single turn; smart-turn v3 gets it to ~200 ms without cutting people off.
This is the highest-leverage latency component in the build, and it's a tuning problem you'll return to repeatedly.

### 4.3 The LLM is one string

Define the model as a streaming OpenAI-compatible `/v1/chat/completions` interface with a configurable `base_url` and
`model`. Local `llama.cpp`, a machine on the LAN, a hardware accelerator, and a cloud API then become the *same code
path*.

This is the single most important architectural decision in the project, because it defers a $115–200 hardware
commitment indefinitely and lets the answer change as prices and models do.

**Default: Groq.** TTFT 120 ms median / 280 ms P95. No retention by default, no training on inputs or outputs, and
self-serve Zero Data Retention rather than an enterprise upsell. The free tier is 14,400 requests/day against a
realistic ~30 — the limit you'll actually hit is 30 requests/*minute*, so design the tool loop with that in mind.

Paid, it's about **$0.05/month** at 30 exchanges a day. A $110 board is roughly nineteen years of inference. **Cost is
not an axis worth optimising here. TTFT and data retention are.**

Failover ladder: Cerebras for latency (0.16 s P50, 1M free tokens/day), GPT-class models with reasoning forced to
minimal for questions the small model fumbles.

> **Google's Gemini free tier is disqualified outright.** Their terms state free-tier content is used to improve Google
> products and that human reviewers may read, annotate and process API input and output. You2's prompts carry the
> user's memory store.

### 4.4 Echo cancellation is not optional

The microphones and the speaker share a small plastic box. Without cancellation the device hears itself, transcribes
its own speech, and responds to it. You cannot interrupt it, and it goes deaf whenever it talks.

Software AEC on a Pi is where these builds die. It needs the playback reference sample-aligned with capture and a
stable known loop delay — and if the mic and speaker are *different* USB devices they run on independent clocks, drift,
and never hold lock.

**Buy hardware AEC where playback and capture are the same UAC2 device.** That gives you one clock domain and an exact
echo reference for free. It is 80% of why the reSpeaker boards are the pick, and it is the one component not to
economise on at any tier.

---

## 5. The memory system

This is the product. Everything above is a way of getting text into and out of it.

### 5.1 Why the obvious design fails

Embed every conversation, put it in a vector store, retrieve top-k at query time. Works for about a month, then quietly
degrades. Three reasons:

**Retrieval noise grows with the corpus.** More fragments means more things that look superficially relevant.
Precision drops exactly as the value of having history should be rising.

**Nothing is ever superseded.** You mention forty different projects over two years and all forty stay equally
retrievable. The system has no notion that thirty-nine are historical.

**No sense of importance.** A throwaway remark and a fact that defines how you work are stored identically.

The result gets *worse* the longer you use it — the precise opposite of the promise.

### 5.2 Three stores

Different questions need different shapes.

**Episodic** — what was said and when. Append-only, embedded, searched fuzzily. The raw record. *"What did I say about
the Anderson thing last month?"*

**Semantic** — durable facts, with identity. Not `"user said they prefer X"` as a blob, but a record with a stable key
that a later statement can **replace**. Each fact carries provenance, a confidence, and a validity window. This is what
makes supersession possible, and supersession is what stops the store from rotting.

Steal Graphiti's **bitemporal** idea even if you don't use Graphiti: every fact gets `valid_at` and `invalid_at`. It's a
mechanism rather than a policy, but it's the best mechanism anyone has.

**Graph** — entities and relationships. People, projects, places, and how they connect. Answers what embeddings are bad
at: *"who else is involved in this?"*, *"what am I working on right now?"* Structured queries where structure exists.

> Do not put Neo4j on a Pi. If you want a graph, **Kuzu** is the only embedded backend that makes sense at this scale.
> For v1, **txtai** over a single SQLite file is enough — and "copy the file" being your entire backup story is worth a
> great deal.

### 5.3 Consolidation

The highest-leverage component, and the one nobody ships.

The device is plugged in and idle eight hours a night. Use them. Re-read the day and:

- Write a summary of each session, so future retrieval hits one summary rather than twenty fragments
- Extract candidate facts, and **supersede** existing ones rather than appending
- Update the graph with new entities and edges
- Decay or archive episodic material that consolidation has already absorbed

The analogy to what sleep does with memory is loose but the intuition is right: you trade raw fidelity for structure,
and that trade is what keeps retrieval sharp at scale.

**Letta's sleep-time compute is built for exactly this** — background agents rewriting consolidated memories while the
device idles. A mains-powered always-on machine has abundant idle time, which turns the project's worst constraint into
its best asset. The consolidation agent can also run a smaller, cheaper model than the primary.

### 5.4 Salience

Something has to decide what's worth keeping, and both failure modes are bad. Remember everything and retrieval drowns.
Remember too little and the device feels like it isn't paying attention — the one thing it must never feel like.

**Start permissive.** Keep raw episodic material for a long window and let consolidation prune, rather than discarding
at capture time when you have the least context about what matters.

### 5.5 Provenance

When You2 tells you something about yourself, it should know where it learned it. This costs almost nothing to carry
and it's the difference between a system you trust and one that's confidently wrong at you. It also gives you a repair
path: *"no, that's out of date"* has to be able to find and fix a specific record.

### 5.6 The rule that protects the hot path

**Every memory write happens after the spoken reply, never in the critical path.** Fact extraction is an extra model
call — run it async once You2 has finished talking.

And **hard-cap the injected memory block** at a token budget (start at 400), enforced in the retriever rather than by
hoping. Retrieval quality is a problem to solve *within* that budget, not by injecting more. Memory bloat feeds directly
back into latency, and it does so gradually enough that you'll misattribute it to hardware.

---

## 6. The connections layer

The "second brain" ambition: it reads what you've told it and finds relationships you'd have missed.

This is the right thing to build. It also contains a specific trap.

### 6.1 Similarity is not usefulness

The obvious implementation embeds everything and surfaces nearest neighbours. Everyone builds it and everyone discovers
the connections are noise. *"You mentioned coffee here, and also here."* Topically related, worthless.

A connection is worth surfacing only if it is **non-obvious, relevant now, and changes something.** Similarity search
clears none of those bars — it finds *related*, which is a different thing.

Worth noticing that Roam and Obsidian backlinks work because a human made each one deliberately. The value was in the
act of noticing, not the automation of it.

### 6.2 The connections that are actually worth having

All temporal or structural rather than semantic:

| Type | Example | Tractable? |
|---|---|---|
| **Contradiction** | "You said X in March and Y today." | Yes — related statements with opposing content |
| **Recurrence** | "Fourth time you've mentioned being wiped out after Tuesday meetings." | Yes — pattern detection over a long log |
| **Open loops** | "Three weeks ago you said you'd look into X; it hasn't come up since." | Yes, and underrated |
| **Bridging** | "This problem is structurally the same as the one you solved in April." | This is the prize, and it's a research problem |

The first three are buildable now. The fourth is analogy rather than similarity, and the two are indistinguishable to an
embedding model.

### 6.3 Push rarely, pull generously

A system that volunteers insights becomes noise fast. One irrelevant *"I noticed something!"* a day and the user stops
reading them permanently — an unrecoverable failure, structurally identical to a false-positive health alert.

**Contradictions and open loops may push**, because a false positive is cheap and a true positive is valuable.
**Bridging may only answer a question**, because a wrong analogy is embarrassing and makes the whole system feel stupid.

### 6.4 What it reads

**Start with conversations only.** They're high-signal precisely because the user chose to say them out loud.

**Add a folder of markdown files second.** Trivial to implement, and it gives you the Obsidian-user story free.

**Don't touch email, calendar or browser history for a long time.** Every source multiplies the noise floor, and the
spurious-connection rate rises faster than the real one.

### 6.5 How you'll know if it works

There is **no benchmark** for "does this feel like it knows me after two years." LongMemEval measures retrieval over
synthetic conversations. You are flying blind on the metric that defines the product.

So build the evaluation loop on day one:

**Rate every connection.** Log everything surfaced, thumbs up or down, one keypress. In three months you have a
precision figure per connection type — and you'll probably find one category carries all the value and two are pure
noise, which tells you exactly what to cut.

**Test against synthetic history.** Fabricate a year of plausible conversation, run consolidation and the connection
engine over it, read the output. You learn in an afternoon what would otherwise take a year of real use, and it costs
an evening of prompt-writing.

### 6.6 What's genuinely unsolved

Extraction, storage and retrieval work today. **Consolidation, contradiction resolution and forgetting do not** — no
framework has a principled decay model. Every system grows monotonically until retrieval degrades.

That's exciting, because there's something real to find. It also means this part is research rather than integration,
and the schedule should say so honestly.

---

## 7. Hardware

### 7.1 Form

A wedge: a 46 mm vertical skirt with an 18° raked face above it.

- **134 × 125 × 165 mm**, 2.4 mm walls, R9 corners, PETG
- Raked face carries the round display, angled so it looks at you rather than the ceiling
- Skirt carries the light bar and the speaker grille
- Four microphone ports on the top face, pushed 16 mm rearward, as far from the driver as the box allows
- Both vents on the **rear face only**

That last point is acoustic, not thermal: front or top vents are leak paths from the driver straight into the mic
array. Likewise the speaker sits in a **sealed chamber** — interior SPL reaches ~130 dB and saturates a MEMS capsule,
and no software recovers a saturated capsule.

The display sets the size. A 115 × 115 mm carrier needs 125 mm of raked face, which sets the height. If a smaller
You2 is wanted, the display is the lever, not the enclosure.

CAD: [`cad/you2.scad`](../cad/you2.scad), parametric, renders clean, both parts export manifold with
`Status: NoError`. Drawings in [`diagrams/`](../diagrams/), renders in [`cad/renders/`](../cad/renders/).

### 7.2 Board — Raspberry Pi 5

**Which SKU depends on where the model runs, and the answer is counterintuitive both ways.**

| SKU | Price (7 Aug 2026) | Verdict |
|---|---|---|
| 2 GB | **$65** | Correct for the API build |
| 4 GB | **$110** | Correct for the local-LLM build |
| 8 GB | $175 | Buys a model you can't usefully run |
| 16 GB | $305 | No |

A DRAM shortage inflated the high-RAM SKUs through four price rises in seven months. Raspberry Pi deliberately held the
1 GB and 2 GB parts at pre-crisis pricing. **Same quad Cortex-A76 at 2.4 GHz, same connectors, same drivers.**

**For the API build: 2 GB.** The local stack has a working set around 700 MB–1.1 GB. 1 GB at $45 works *only* with a
native framebuffer UI — kiosk Chromium alone is 900 MB–1.2 GB and will swap.

**For the local build: 4 GB, not 8 GB.** Decode is memory-bandwidth-bound at ~17 GB/s and that is identical across
SKUs:

| Model @ Q4 | Size | Decode ceiling |
|---|---|---|
| 2B | ~1.5 GB | ~11 tok/s |
| 4B | ~2.5 GB | ~6.8 tok/s |
| 8B | ~4.5 GB | ~3.8 tok/s |

At 3.8 tok/s a 15-token sentence is four seconds before first audio. The only model that runs at tolerable speed is
2B-class, and that fits in 4 GB beside the audio stack. **More RAM buys a model you can't use** — and the extra $65 is
exactly what decides whether you get a real display.

**Not a Pi 4.** Pi 4 2 GB is $55, a $10 saving; Pi 4 4 GB is $100 and 8 GB is $165, both *more* than the Pi 5 2 GB that
beats them. The Pi 5 is ~2× single-core: Moonshine goes 237 ms → 500–600 ms, and Piper medium measures RTF 0.357 on Pi
4 *using all four threads*, which you don't have. Realistic Pi 4 round trip is **~1.7 s**.

Runner-up was the Radxa ROCK 5C at $34.90 — genuinely fast enough, but it costs a Rockchip vendor BSP kernel, a
fan-dependent thermal profile next to a microphone array, and a supply situation where 12 of 15 SKUs were out of stock.
Not worth $30 on a latency-critical always-on device.

### 7.3 Display — Waveshare 3.4″ DSI round, 800×800, ~$87

⌀87.6 mm of glass on a 115 × 115 mm carrier. In the mainline kernel — setup is one line:
`dtoverlay=vc4-kms-dsi-waveshare-panel,3_4_inch`. Touch runs on I2C-0 *inside the DSI cable*, so header I2C1 stays free.

**DSI, not DPI.** The Pi 5 has two spare 4-lane MIPI transceivers, so DSI costs **zero GPIO** and leaves the entire
40-pin header free. Whereas the only off-the-shelf round DPI panel consumes GPIO 0–27 with nothing spare — not even a
pin for the LED data line.

Two things that will cost you an afternoon each:

- **The box ships 15-pin cables. A Pi 5 needs 22-pin.** Add Adafruit #5822 ($2). Wrong cable gives a dead black screen
  with no error message.
- **Feed the panel 5 V from the header or its Type-C port.** The DSI FPC cannot supply enough current, and skipping this
  gives a display that half-works.

### 7.4 Audio — Seeed reSpeaker over USB

**reSpeaker Lite (~$25)** for two mics, or **Flex XVF3800 (~$55)** for a four-mic circular array on a 20 cm FPC ribbon
that lets you mount the array flat under the top face and tuck the core board anywhere.

Both are XMOS silicon doing hardware AEC, both enumerate as UAC2 with no drivers, and both put playback and capture on
the same clock domain. Drive the speaker from the reSpeaker's own output so the AEC sees the correct reference.

**USB, never I2S** — see [Standing decisions](#11-standing-decisions).

### 7.5 Light bar — 2× NeoPixel Stick 8

A ring around the eye was the original concept and it doesn't survive contact with parts: the largest stocked NeoPixel
ring is ⌀65.5 mm outer, so against ⌀87.6 mm glass it would sit *behind* the display. Two 8-LED sticks butted end to end
give a 102 mm bar across the skirt, which is also far easier to diffuse than a torus.

The bar is not decoration. A voice device must signal its state — idle, listening, thinking, speaking — and light is the
only channel that does it without interrupting you.

Drive it with `dtoverlay=ws2812-pio,gpio=18,num_leds=16`. On the Pi 5 this is in-tree, works on any GPIO, needs no root,
and exposes `/dev/leds0`. (The old `rpi_ws281x` DMA path *is* dead on Pi 5 — don't copy a 2019 tutorial.)

**Electrically**, this is the only real circuit in the build:

```
Pi GPIO (3.3 V) → 74AHCT125 (Vcc = 5 V) → 330 Ω → WS2812 DIN
                                            ↑
      separate 5 V / 2 A supply ────────────┴── 1000 µF across 5 V/GND at the strip
                  │
      common ground with the Pi   ← mandatory
```

Four classic failures: no level shifter (3.3 V is inside WS2812's *undefined* logic band — works on the bench, glitches
in the enclosure); LEDs powered from the Pi's 5 V pin (WS2812 draws ~18 mA per *channel*, not per LED, so a 16-ring at
full white is ~0.9 A — clamp brightness to 0.3); no common ground; no bulk capacitor.

### 7.6 Power and cooling

**Buy the official 27 W USB-C PD supply.** The Pi 5 silently caps total USB current at 600 mA unless it detects
Raspberry Pi's own PD profile, which most third-party "27 W" bricks don't implement. A bus-powered mic array exceeds
that, and the failure mode is your audio device vanishing mid-session — which you will spend days blaming on ALSA.

**Passive cooling, no fan.** The official Active Cooler is 35–40 dBA at 0.5–1 m; your mics sit 50–80 mm away on the same
rigid chassis, so it presents at roughly 55 dBA at the capsules against speech at ~60. That's ~5 dB SNR where far-field
ASR wants 20–25. A fan doesn't degrade your wake word, it deletes it. And the XMOS chip cancels far-end echo from the
speaker — it does **not** cancel near-field mechanical noise from 10 cm away.

This works because the load is bursty, not sustained: a voice turn is 2–5 s, and passive Pi 5 cooling only throttles
after 200–300 s of continuous full load. **Removing local inference is what makes silent operation possible.**

---

## 8. Bill of materials

Full detail with links in [`bom.md`](bom.md). Summary:

| Build | Total | Model | Round trip |
|---|---|---|---|
| **API** — Pi 5 2 GB | **$248** | 8B–70B, your choice | 620–780 ms always |
| **Local** — Pi 5 4 GB | **$282** | 2B | ~1 s warm, 5–7 s cold |
| Cheapest — HDMI display + 1 GB | $185 | API only | 620–780 ms |

The local build costs $34 more and is slower with worse answers. It buys exactly one thing: nothing ever leaves the
room. That's either the point of the project or it isn't — a values call, not a technical one.

**The software is identical either way.** Buy the 4 GB board, run against Groq while building, switch to `llama.cpp`
when you want. Same code, same enclosure, no rework — which defers the decision until you've lived with the thing.

### Making the local build tolerable

A 2B model prefills at ~60–80 tok/s. A 400-token memory block costs ~5–7 s cold and ~1 s on a cache hit; uncapped at
1000+ tokens it's 12–17 s cold. That's bimodal, and bimodal is unshippable. Three mitigations, designed in from the
start:

1. **Hard-cap the injected memory at ~400 tokens**, enforced in the retriever.
2. **An intent router** — timers, time, "what did I say about X" answered locally in ~200 ms without touching the model.
   That covers most real utterances and keeps working when the network is down.
3. **Rebuild the memory block nightly, not per-query.** The highest-leverage one. A stable daily profile keeps the prefix
   cache warm so you live in the 1-second case, with query-specific retrieval in a small addendum placed *after* the
   cache breakpoint. Trades a little precision for an order of magnitude of latency.

---

## 9. Build sequence

### Order first

Long lead times, and one of them is the only thing simulation can't touch:

| Item | Why now |
|---|---|
| reSpeaker | Longest lead, highest risk, and barge-in cannot be tested any other way |
| Display + 22→15-pin FFC | Ships overseas, thin stock |
| Pi 5, PSU, heatsink, microSD | Core |

**You don't need the display to start.** The three things that can sink this project — Moonshine's real latency,
barge-in, and the end-to-end budget — are all testable with no screen. Order it for availability, not urgency.

**Defer the LEDs.** They're $19 and they are the *only* soldering in the entire build; everything else is USB, a DSI
ribbon, and a JST plug. Nothing risky depends on them.

### While parts ship

Build the software on your laptop. The memory system especially — it needs no hardware at all and it's the actual
product. Point the LLM at Groq's free tier. **The goal is that when the box arrives you have a working assistant that
just needs a body.**

### Day 1 — kill the two things that can invalidate the design

**The Moonshine integration path is the highest-risk item in the build.** The Python `useful-moonshine-onnx` one-shot
API takes **~3 s for a 5-second clip on a Pi 5**. The C++ streaming library on identical silicon takes **237 ms** — a
12× gap caused entirely by API choice, because the Python path re-encodes the whole clip every call with no state
caching.

> **If you prototype in Python you will measure 3 seconds, conclude the Pi 5 is too slow, and go shopping for a
> Jetson.** Build the streaming C++ library with cached encoder/decoder state *first*, before evaluating anything else.

**Then barge-in.** Long TTS playback at normal volume; say the wake word over the top of it from two metres. If that
fails, nothing else about the device matters — and it is far cheaper to learn now than after six enclosure revisions.

### Day 2–7

| Day | Work |
|---|---|
| 2 | Display up: `dtoverlay=vc4-kms-dsi-waveshare-panel,3_4_inch`, and feed the panel 5 V separately |
| 3 | Run the full stack concurrently, measure peak RSS. Settles the RAM question for unit two |
| 4 | Measure TTFT from the actual desk on the actual Wi-Fi across a full day. Then decide about Ethernet |
| 5 | Assemble the loop end to end with a timestamp on every stage boundary |
| 6–7 | Thermals under bursty load; confirm you aren't tripping the 600 mA USB limit |

Instrumenting every stage boundary on day 5 matters more than it sounds: when you're over budget, the log tells you
which stage to attack instead of sending you board-shopping.

### Then, and only then, the body

Measure the real parts with calipers → update the CAD parameters → print. Every dimension currently in the model came
from a datasheet, and the first thing the real display will do is prove one of them wrong.

If you don't own a printer, the shell is another $30–60 and about a week from a service — and at 165 mm tall it exceeds
some hobby printer build volumes.

---

## 10. What can be tested without hardware

Roughly 80% of this project can be built and validated before a part arrives.

**The memory system — 100% testable.** No hardware at all. And you can run the test that normally takes a year:
generate six months of synthetic conversation, load it, run consolidation, and measure whether retrieval quality holds.
That degradation is the failure mode that kills every memory system, and you can find it on a Tuesday.

**The whole voice pipeline — runs on your laptop.** All five components plus a Groq call. What transfers: correctness,
stage breakdown, prompt quality, the tool loop. What doesn't: absolute timing, since your laptop is several times faster
than an A76. Instrument anyway — the *ratios* tell you where to attack.

**The screen — it's a web page.** 800 × 800 in a browser with a circular mask. Iterate the four states, the waveform and
the transcript with instant reload instead of reflashing an SD card.

**Network RTT — measurable today.** One term of the latency budget, free to check, from the desk where the device will
live. Sample across a day so you see variance, not just median.

**The ARM toolchain — Docker arm64 on Apple Silicon** runs at native speed. Won't give Pi-accurate timing but proves the
builds work, which matters most for Moonshine.

**The wiring — KiCad.** Draw the schematic, run ERC. It catches unconnected pins and missing power flags, becomes the
document you follow while building, and is the starting point for a tidy-up PCB later. Falstad's browser simulator if
you want to watch the level shifter behave.

**Physical fit — import STEP models.** Raspberry Pi publish them for the Pi 5. Check clearances and cable routing in
FreeCAD before printing a 10-hour shell. This catches "the connector faces the wrong way."

**What cannot be simulated:** acoustics, echo cancellation, enclosure resonance, and thermals in the real box. Three
known unknowns rather than a fog — and none of them are design errors, which is the class simulation is for.

---

## 11. Standing decisions

Things not to relitigate without a specific reason.

**Under a second, or nothing else matters.** Latency is the product. Overlap every stage; never wait for generation to
finish.

**The model is a commodity; the memory is not.** Don't spend six months on inference optimisation and two weeks on
memory.

**Consolidate, don't accumulate.** A memory system that only appends gets worse with use.

**The LLM is one `base_url` string.** Local, LAN, and cloud must remain the same code path forever.

**Audio stays on USB.** The reSpeaker has an I2S mode. Taking it re-imposes the GPIO conflict that USB freed you from,
to chase milliseconds that aren't the bottleneck — 237 ms of ASR is. Do not let a "just try I2S to save a few ms"
experiment quietly undo this.

**No fan anywhere near the microphones.**

**No actions, no feed, no notifications.** It speaks when spoken to.

**Wake-word activated, never always-recording.**

---

## 12. Risks and open questions

### Ranked by how much they'd hurt

**1. Barge-in fails.** The device hears itself and can't be interrupted. Mitigated by hardware AEC on a single UAC2
device, but unprovable until hardware exists. *Test day one.*

**2. Memory degrades over months** in a way you misattribute to hardware. The retrieved block enters every prompt, so
bloat feeds directly into latency. Mitigated by the token cap, nightly consolidation, and the rating loop.

**3. The connections layer is mostly noise.** Genuinely possible. Detectable early via synthetic history; recoverable by
cutting the categories that don't earn their place.

**4. Moonshine's real-world accuracy.** Tiny is 12.0% WER against Small's 7.84%, but Small costs 527 ms and blows the
budget. Acceptable for a constrained desk vocabulary; the escape hatch is Moonshine's specialised-model work.

**5. Latency variance.** You've swapped a bounded local number for an unbounded network one. Provider incidents, Wi-Fi
congestion and the 30 req/min free-tier cap all become sources of a bad turn. Wire Ethernet if you can.

**6. Offline behaviour.** On the API build, no network means You2 wakes, transcribes, and has nothing to say. Design a
graceful offline mode — local intents for time, timers, and memory lookup — or accept a brick.

### Open questions

- Does the A76 hold the sub-second budget with all five components running concurrently? Every benchmark cited is
  single-workload and therefore optimistic.
- What's the real peak RSS of the full stack? No published figure exists for Moonshine.
- Does Moonshine ship prebuilt ARM binaries, or is a source build required?
- Which vendors publish STEP models for the specific display and mic array?
- Does the 5.06 mm of material at each end of the raked face flex when you press the screen?

---

## 13. Corrections log

Things believed during design that turned out to be wrong. Kept because the reasoning is reusable, and because a
correction you can't see is a correction you'll make again.

| Believed | Actually |
|---|---|
| The cheap fallback is a used Pi 4 | Pi 4 2 GB saves $10 and misses the latency requirement (~1.7 s round trip). Pi 4 4 GB and 8 GB cost *more* than the Pi 5 2 GB that beats them. |
| An 8 GB Pi is needed for local inference | Decode is bandwidth-bound at ~17 GB/s regardless of capacity. 4 GB holds the only model that runs at tolerable speed. More RAM buys a slower device. |
| Dropping I2S reopens DPI displays | Killing I2S buys back 4 pins of 28. Every DPI mode still eats I2C1, UART and most of SPI0 — and the only round DPI panel uses all 28 with nothing spare. |
| There's a 480×480 round SPI display | There isn't. That panel is ST7701S — no internal GRAM, fed over RGB parallel, i.e. it *is* DPI. Round SPI means GC9A01 240×240 and nothing larger. |
| Pi 4 is easier for WS2812 | Backwards. Pi 5 has an in-tree PIO overlay: any GPIO, no root, one config line. Pi 4 needs root, offers four pins, and wants audio disabled. |
| The display box includes both cable types | 15-pin only. A Pi 5 needs 22-pin, and the wrong one gives a black screen with no error. |
| Thin and light drive adhesive wear time *(from the earlier wearable exploration)* | Wrong by 300–1000×. Mass-and-height contributes ~37 Pa against 10,000–30,000 Pa of substrate strain mismatch. |
| Legs cool overnight *(same)* | Distal skin temperature *rises* 1–4 °C at sleep onset. |

**The pattern worth internalising:** every one of these was a load-bearing claim that could have been checked in an
afternoon, and several would have cost real money. Two habits catch most of them — read the incumbent's actual manual
and price list, and dimension-check every load-bearing number against a second independent source.

---

*Repo layout, renders and CAD: see the [README](../README.md).*

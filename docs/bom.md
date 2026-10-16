# Bill of Materials

> ## ⚠ This is not the shopping list. **[docs/build.html](build.html) is.**
>
> Every costing this file used to carry has been deleted: the $248 API build, the $282 local variant, the $295 / $442 /
> $650 tiers, the $344 buy-first list, and the shopping links behind them. They were not merely stale. All five bought
> the two-microphone **reSpeaker Lite** for an enclosure built around the four-microphone **Flex XVF3800 Circular-4**,
> most of them bought a WS2812 ring the design dropped for a bar and then dropped entirely, three bought the wrong
> Raspberry Pi, and none of them bought a single fastener, gasket or acoustic membrane. Following any of them gets you
> a box of parts that does not go in the box.
>
> **Parts come to ≈ $351**, and the list that is derived from the model rather than from a research pass is in
> [build.html](build.html).
>
> What is left here is the part that was never about prices: why the LLM moved off the device, what that decision
> deleted from the hardware, the software stack, and the five things most likely to go wrong. That analysis is still
> live, and it is why this file was gutted rather than removed.

## The board: Raspberry Pi 5 **2 GB** — $65

[pishop.us](https://www.pishop.us/product/raspberry-pi-5-2gb/) · [canakit.com](https://www.canakit.com/raspberry-pi-5-2gb.html)

The 8 GB SKU was $175 only because of the DRAM shortage, and 8 GB was only ever there to hold a local model. Raspberry
Pi have deliberately held the 1 GB and 2 GB SKUs at pre-crisis prices through all four 2026 hikes. **Same quad
Cortex-A76 @ 2.4 GHz, same connectors, same drivers. $110 off for zero engineering risk** — a bigger saving than any
alternative board in the survey delivers.

The local stack (openWakeWord + Silero + smart-turn v3 + Moonshine Tiny + Piper + sqlite/embeddings) has a working set
around 700 MB–1.1 GB. 1 GB at $45 works **only** with a native framebuffer/LVGL UI — a kiosk Chromium is 900 MB–1.2 GB
on its own and will swap. $20 is the cheapest insurance in this BOM.

### Measured latency budget on A76 @ 2.4 GHz

| Stage | Cost |
|---|---|
| openWakeWord (always-on) | <5 ms / 80 ms frame (~6% of one core) |
| Silero VAD | <1 ms / 30 ms chunk |
| smart-turn v3 | 60–120 ms on ARM (the 12 ms figure is desktop x86 — don't use it) |
| **Moonshine Tiny streaming ASR** | **237 ms** |
| Groq TTFT + home RTT | 120–180 ms + 40–80 ms |
| Piper medium, first sentence | ~165 ms (RTF 0.107–0.119) |
| **Total** | **~620–780 ms** |

### ⚠️ Correction: not a Pi 4

An earlier draft of this file said the cheap fallback was a used Pi 4B. It isn't.

- Pi 4 2 GB is $55 — a **$10** saving. Pi 4 4 GB is $100 and Pi 4 8 GB is $165, both *more* than the Pi 5 2 GB.
- Pi 5 is ~2× single-core. Moonshine Tiny goes 237 ms → ~500–600 ms. Piper medium measures RTF 0.357 on Pi 4 *using all
  four threads*, which you don't have — at two available threads a 1.5 s sentence costs ~720 ms.
- **Realistic Pi 4 round trip: ~1.7 s.** It misses the product requirement to save $10.

Runner-up was the **Radxa ROCK 5C** at $34.90 — genuinely fast enough (A76-class), but it costs you a Rockchip vendor
BSP kernel, a fan-dependent thermal profile next to a microphone array, and Radxa's supply situation (12 of 15 SKUs out
of stock on their US distributor). $30 isn't worth a BSP dependency on a latency-critical always-on device.

## The display: Waveshare 3.4″ DSI round, 800×800 — ~$87

[The Pi Hut](https://thepihut.com/products/3-4inch-dsi-round-touch-display-800x800) (£81.60 inc VAT ≈ $87 ex-VAT) ·
[wiki](https://www.waveshare.com/wiki/3.4inch_DSI_LCD_(C))

In the mainline kernel — setup is one line: `dtoverlay=vc4-kms-dsi-waveshare-panel,3_4_inch`. Touch runs on I2C-0
*inside the DSI cable*, so header I2C1 stays free. **Feed it 5 V from the header or its Type-C port** — the DSI FPC
can't supply enough current, and skipping this gives a display that half-works.

Stock is thin (3 units when checked). Drop-in alternative: [Waveshare 4″ 720×720 DSI round](https://thepihut.com/products/4-dsi-round-touch-display),
~$92, 10 in stock. Avoid the Newegg $133.99 listing — 1.5× markup.

### ⚠️ Correction: the DPI question, settled

I claimed dropping I2S reopened DPI displays. **The mechanism was wrong.** Per Raspberry Pi's own whitepaper RP-003471-WP,
DPI's footprint is mode-dependent, and killing I2S buys back **4 pins out of 28**. Every DPI mode still consumes I2C1
(GPIO 2/3), UART (14/15) and most of SPI0 (7–11).

And it's moot anyway: the only off-the-shelf round DPI panel (HyperPixel 2.1 Round) uses **GPIO 0–27 with nothing
spare** — no pin left for the WS2812 data line. Meanwhile the **Pi 5 has two spare 4-lane MIPI transceivers**, so DSI
costs zero GPIO and leaves the entire 40-pin header free.

### ⚠️ Correction: there is no 480×480 round SPI display

I put one in an earlier BOM. Round SPI on a Pi means **GC9A01 240×240 at 1.28″ and nothing larger**. The 2.1″ 480×480
panel is ST7701S — no internal GRAM, fed continuously over RGB parallel, i.e. it *is* a DPI panel. The genuinely cheap
fallback is [Waveshare 1.28″ GC9A01 at $16.95](https://www.pishop.us/product/240-240-general-1-28inch-round-lcd-display-module-65k-rgb/),
but 32.4 mm of glass is an indicator lamp, not a display.

### ⚠️ Correction: Pi 5 is *easier* for WS2812, not harder

| | Pi 4 | Pi 5 |
|---|---|---|
| Path | `rpi_ws281x` PWM/DMA | `dtoverlay=ws2812-pio` (in-tree, RP1 PIO) |
| Pins | 4 candidates only | **any GPIO 0–27**, up to 4 chains |
| Root | required | not required — exposes `/dev/leds0` |
| Extra config | `dtparam=audio=off`, pinned `core_freq` | none |

The old DMA path *is* dead on Pi 5 — don't copy a 2019 tutorial — but the replacement is one config.txt line.

### What the API decision deleted from the hardware

**No NVMe + M.2 HAT** (−$40–50). Those existed to load a multi-GB GGUF fast. A microSD is fine now.

**No active cooler** — and this fixes a design problem, not just cost. The local stack is bursty, not sustained; there's
no all-core inference load any more, so a large passive case works. That matters because **the XMOS AEC cancels far-end
echo from the speaker, not near-field mechanical fan noise from 10 cm away.** Removing the local LLM is what makes
silent operation possible.

**No I2S wiring, no DAC/amp HAT, no GPIO conflict resolution.** USB audio + DSI means the 40-pin header carries nothing
but the WS2812 data line and its ground.

**The PSU does not get cheaper.** Keep the genuine 27 W — an under-spec supply makes the Pi 5 current-limit its USB
ports to 600 mA total, and you're hanging a bus-powered mic array off them.

## The API layer

**Groq**, `llama-3.1-8b-instant`, streaming on, reasoning forced off.
`https://api.groq.com/openai/v1/chat/completions` — native OpenAI-compatible with SSE.

- **TTFT 120 ms median / 280 ms P95.** You have ~300–400 ms of budget for it. Fits with margin.
- **Best privacy posture in the field**: no retention by default, no training on inputs/outputs, **self-serve Zero Data
  Retention** rather than an enterprise upsell. Enable ZDR before sending a real prompt.
- **Free tier runs this indefinitely**: 14,400 req/day against your ~30. The limit you'll actually hit is **30
  req/minute** — design the tool loop with that in mind.

If you pay: **$0.05/month** at 30 exchanges/day. Even Claude Haiku 4.5 is $1.35. *A $65 Pi is roughly 19 years of
inference at this volume — cost is not an axis worth optimising. TTFT and retention are.*

Failover ladder: **Cerebras** (0.16 s P50, 1 M free tokens/day) for latency, **GPT-5.6 Luna** ($0.20/$1.20, reasoning
minimal) for queries the small model fumbles.

**Google's Gemini free tier is disqualified outright** — their terms state free-tier content is used to improve Google
products and that "human reviewers may read, annotate, and process your API input and output." Your prompt carries the
user's memory store.

### ⚠️ Two API traps

**Drop prompt caching from v1.** A 1,000-token prompt sits in a caching dead zone by construction: OpenAI activates at
≥1,024 tokens, Claude Haiku's minimum cacheable prefix is 4,096, Gemini's is 4,096. Groq caches only three models at a
flat 50%, hits "not guaranteed." And when it works it saves **11 cents a month**. Implement it later for the *TTFT*
benefit, not the money.

**Published TTFT for reasoning models includes thinking time.** GPT-5.6 Luna measures 2.60 s at medium and 10.96 s at
high; Gemini 3.5 Flash-Lite 10.26 s. Not comparable to Groq's 120 ms. **Force reasoning off explicitly and re-measure
yourself** — and note every figure here was measured datacenter-side, so add your real home RTT.

## Week one

**Day 1 — kill the two things that can invalidate the design.**

**The Moonshine integration path is the highest-risk item in the build.** The Python `useful-moonshine-onnx` one-shot
API takes **~3 s for a 5-second clip on a Pi 5**. The C++ streaming library on the same silicon takes **237 ms** — a
12× gap caused entirely by API choice, because the Python path re-encodes the whole clip every call with no state
caching. **If you prototype in Python you will measure 3 s, conclude the Pi 5 is too slow, and go shopping for a
Jetson.** Build the streaming C++ library with cached encoder/decoder state first.

Then `dtoverlay=ws2812-pio,gpio=18,num_leds=16` and check `/dev/leds0` appears.

**Day 2** — display up: `dtoverlay=vc4-kms-dsi-waveshare-panel,3_4_inch`, and feed the panel 5 V separately.

**Day 3** — run the full stack concurrently and measure peak RSS. Settles 1 GB vs 2 GB for the next unit.

**Day 4** — measure TTFT from the actual desk on the actual Wi-Fi. Then decide about Ethernet.

**Day 5** — assemble the loop end to end with a timestamp on every stage boundary, so when you're over budget the log
tells you which stage to attack instead of sending you board-shopping.

**Day 6–7** — thermals under bursty load, and confirm you're not tripping the 600 mA USB limit.

### Standing rule

**Don't let a "just try I2S to save a few ms" experiment quietly re-impose the original constraint.** The reSpeaker has
dual I2S/USB modes. The USB/UAC2 decision is what freed the entire 40-pin header and made the display question easy.
USB latency isn't the bottleneck — the 237 ms of ASR is. Leave it on USB.

---

## Variant: local LLM on-device

Same enclosure, same software, one `base_url` string different. The costing that
was here has gone with the rest; what survives is the argument about *which*
board, which is not a price question.

### Buy the 4 GB ($110), not the 8 GB ($175)

For local inference on a Pi 5, more RAM buys a model you cannot usefully run. Decode is memory-bandwidth-bound at
~17 GB/s and that is **identical across SKUs**:

| Model @ Q4 | Size | Decode ceiling |
|---|---|---|
| 2B | ~1.5 GB | ~11 tok/s |
| 4B | ~2.5 GB | ~6.8 tok/s |
| 8B | ~4.5 GB | ~3.8 tok/s |

At 3.8 tok/s a 15-token sentence is four seconds before first audio. The only model that runs at tolerable speed is
2B-class, and that fits in 4 GB beside the ~1.1 GB audio stack. **The extra $65 for 8 GB is exactly the $65 that
decides whether you get a real display.**

### What it delivers, honestly

A 2B model prefills at ~60–80 tok/s, so a 400-token memory block costs ~5–7 s cold and ~1 s on a cache hit. Uncapped at
1000+ tokens it is 12–17 s cold. **That is bimodal, and bimodal is unshippable.** Three mitigations, all designed in
from the start:

1. **Hard-cap the injected memory at ~400 tokens**, enforced in the retriever.
2. **Intent router** — timers, time, lights, "what did I say about X" answered locally in ~200 ms, never touching the
   model. That covers most real utterances.
3. **Rebuild the memory block nightly, not per-query.** The highest-leverage one: a stable daily profile keeps the
   prefix cache warm so you live in the 1-second case, with query-specific retrieval in a small addendum placed *after*
   the cache breakpoint. Trades a little retrieval precision for an order of magnitude of latency.

### Local vs API

Ignore the absolute figures below — they came from a costing that no longer
exists. The ratios and the latencies are what the row is for.

| | Local | API |
|---|---|---|
| Round trip | ~1 s warm, 5–7 s cold | 620–780 ms always |
| Model | 2B | 8B–70B |
| Offline | yes | no |
| Memories leave the house | never | every turn |
| Recurring | $0 | $0 on Groq free tier |

The local build costs a little more and is slower with worse answers. It buys exactly one thing: nothing ever leaves
the room. That is either the point of the project or it isn't — a values call, not a technical one.

**The software is identical either way.** Buy the 4 GB board, run against Groq while building, switch to llama.cpp when
you want. Same code, same enclosure, no rework — which defers the decision until you have lived with the thing.

---

## The verdict on local LLM

**The recommended tier cannot run a useful memory-augmented model locally at conversational speed. Ship v1 hybrid.**

Every benchmark you'll find quotes *decode* tokens/sec, which is the wrong metric here. You2 injects retrieved memories into every prompt, so the prompt is 500–1500 tokens, not 20. A Pi 5 prefills at **38 tok/s** on a 3 B model — a 1000-token memory-augmented prompt is **~26 seconds before the first token exists**, regardless of decode rate.

KV prefix caching helps enormously. Structure every prompt as `[stable persona + stable memory] + [volatile recent turns]`, run `cache_prompt=true` with `--ubatch 2048`, and you only prefill the volatile tail. **But the moment the memory block changes — which for a memory product is the entire point — you take a full cache miss and stall 8–25 s.** You cannot ship bimodal response times.

**On the wedge, always:** wake word, VAD + turn detection, ASR, TTS, hardware AEC, the memory store, and an **intent router** — a small local classifier for timers, weather, "what did I say about X" that answers in ~200 ms, never touches the LLM, and keeps working when the LAN box is off. That covers most daily utterances.

**Off the wedge:** the reasoning layer only. A used Lenovo M720q runs `llama-server` with Qwen3.5-4B or 9B; prefill on an i5-8500T is 150–250 tok/s and wired GbE adds under a millisecond. Or any OpenAI-compatible API — memory, transcripts and audio still never leave the house.

**Make it reversible:** define the LLM as one swappable interface, streaming `/v1/chat/completions`. Local llama.cpp, LAN box, Hailo, and cloud API are then the same code path, and the brain can move later without touching the pipeline or the enclosure.

**And the single decision that matters most:** never wait for the LLM to finish. A 40-token reply at 8.8 tok/s is 4.5 s of generation. **Sentence-chunk the token stream into Piper as clauses land.** First audio reaches the speaker in ~600 ms while generation continues underneath — 4.5 s becomes 600 ms of *perceived* latency. Every "local assistant that feels broken" skipped this.

---

---

## Software

| Stage | Pick | License | Note |
|---|---|---|---|
| Wake word | **openWakeWord**, custom **"Hey Friday"** | Apache 2.0 | Train your own on Colab (~75–90 min) from synthetic Piper audio. The *bundled* models are CC BY-NC-SA — training your own also escapes that. |
| VAD | Silero VAD v6 | MIT | <1 ms/chunk, cheap gate only |
| Turn detection | **pipecat smart-turn v3** | BSD-2 | ~65 ms on one core. **Highest-leverage latency component in the build** — a naive 700 ms silence timeout burns 70% of your budget every turn. |
| Orchestration | **pipecat** ≥ 0.0.85 | BSD-2 | Streaming and barge-in are first-class |
| ASR | **Moonshine streaming** (tiny 44 M / small 123 M) | — | Not Whisper — whisper.cpp pads every clip to 30 s, so a 3-second utterance costs 30 seconds of compute. Moonshine *finishes* when you stop talking. Use its own ONNX path, not HF Transformers. |
| LLM | Qwen3.5-4B/9B Q4_K_M on the LAN box | — | Qwen3.5-2B local as offline fallback |
| TTS | Piper (`piper1-gpl` 1.6.0) → Kokoro-82M | **GPL-3.0** | Piper moved MIT → GPL at the OHF-Voice fork. Keep it behind an HTTP/CLI boundary if you might ship closed. |
| Memory v1 | **txtai** | Apache 2.0 | One SQLite file *is* the second brain. "Copy the file" is the whole backup story. |
| Memory v2 | **Letta** | Apache 2.0 | Adopt for real consolidation. Sleep-time compute is the decisive feature — background agents rewrite memories while idle, and a mains-powered always-on device has abundant idle. Turns the worst constraint into the best asset. |

**Wyoming as the backbone? No.** There is no standalone Wyoming pipeline runner — the orchestrator only exists inside Home Assistant, so adopting it means installing a home-automation platform on a desk companion or writing the orchestrator anyway. Use its Docker containers as convenient prebuilt services if you like; don't adopt it as architecture. Also ignore Rhasspy 3 (archived), Coqui XTTS (company gone, non-commercial license most tutorials omit), and Willow (momentum stopped).

**Gemma 4 E2B's "E" is *effective* parameters** — 2.3 B of compute but 5.1 B of weights to load. Budget RAM against 5.1 B or you'll OOM on an 8 GB board also running ASR and TTS.

---

---

## The five things that will go wrong

**1 · Prefill.** Covered above. Cap the injected memory block at a hard token budget (start at 400) enforced in the retriever, not by hoping. Pick hardware on prompt-eval throughput and TTFT, never decode t/s.

**2 · Barge-in won't work, and you'll find out at the end.** The speaker is centimetres from the mics in a rigid box. Wake word re-triggers on its own TTS; you can't interrupt it; it goes deaf whenever it speaks. Software AEC on a Pi is where these builds die — it needs the playback reference sample-aligned with capture, and if mic and speaker are *different* devices they run on independent clocks and drift out of lock permanently.

→ Buy hardware AEC where playback and capture are the **same UAC2 device**. That's one clock domain and an exact echo reference for free, and it's 80% of why the reSpeaker boards are the pick. **Test barge-in on day one**: long answer at normal volume, wake word spoken over the top from 2 m. If that fails nothing else matters, and it's much cheaper to learn now than after six enclosure revisions. Never put mics in the speaker chamber — interior SPL hits ~130 dB and saturates the MEMS element; no software recovers a saturated capsule.

**3 · Driver support — four silent failures, all with 2024-era tutorials ranking first on Google.**
- **Pi 5 I2S capture** routes through the RP1 southbridge. INMP441 guides give you a card that enumerates, accepts `arecord`, and **returns silence**. Every pre-2024 overlay is wrong. → Go USB; the reSpeaker sidesteps it entirely.
- **You cannot stack two I2S overlays.** One I2S peripheral; only the last one loads, silently killing the other.
- **`rpi_ws281x` is dead on Pi 5** — it drove NeoPixels via PWM/DMA blocks that moved behind RP1, and fails with `ws2811_init failed with code -3`. → The 2026 method is RP1 **PIO**: install `Adafruit-Blinka-Raspberry-Pi5-Neopixel`. If `/dev/pio0` is missing, `sudo apt full-upgrade` + `rpi-eeprom-update -a` + reboot, and add `SUBSYSTEM=="*-pio", GROUP="gpio", MODE="0660"` to `/etc/udev/rules.d/99-com.rules`.
- **The DPI/I2S pin collision — decide before buying.** Every DPI display drives the panel over GPIO 0–21. **I2S lives on GPIO 18–21, the same physical pins.** The documented fix relocates I2S to GPIO 28–31, which aren't broken out on any 40-pin header. On a Pi 5 this isn't a tradeoff, it's an impossibility. → **DSI**, which uses the dedicated MIPI connector and touches zero GPIO. That's the entire argument for the 3.4-DSI-TOUCH-C.
- Two DSI footguns: Pi 5 uses a **22-pin 0.5 mm** FFC, not Pi 4's 15-pin (wrong cable = dead black screen, no error), and the output enumerates as **`DSI-2`**, breaking every copy-pasted `wlr-randr` command. The near-identical older 3.4″ part also uses a different overlay name — write the part number on the box.

**4 · The fan will delete your wake word.** The official Active Cooler is 35–40 dBA at 0.5–1 m. Your mics sit 50–80 mm away on the same rigid chassis, so inverse-square alone puts it at **~55 dBA at the capsules** plus structure-borne vibration. Speech at 1 m is ~60 dBA. That's **5 dB SNR** against the 20–25 dB far-field ASR needs. Not degraded — deleted.

→ Your load is *bursty*, not sustained: a voice turn is 2–5 s and passive Pi 5 cooling only throttles after 200–300 s of continuous full load. Big passive heatsink plus designed-in convection handles steady state silently. Put **both** vent paths on the **rear face** — front or top vents become acoustic leak paths into the mic array. Set `fan_temp0=70000` in `/boot/firmware/config.txt` (*not* `/boot/config.txt`, which fails silently) so it never spins normally, and force fan PWM to 0 on wake-word trigger. And spend $17 on the **Noctua NF-A4x10 5V** — 17.9 dBA at full speed versus 35–40, roughly a tenth the perceived loudness. Buy the 5 V version; 12 V will destroy it.

**Related, and it bites first:** the Pi 5 silently caps total USB current at **600 mA** unless it detects a genuine 5 A PD source using a Raspberry Pi custom PD profile that most third-party "27 W" bricks don't implement. A USB mic array plus one peripheral exceeds that. The failure mode is intermittent dropouts and your audio device vanishing mid-session, which you'll spend days blaming on ALSA. **Buy the official brick.** Same story on LEDs: WS2812 draws **18 mA per channel, not per LED** — a 24-ring at full white is ~1.30 A. Clamp brightness ≤0.3, add 470–1000 µF across 5 V/GND, 300–500 Ω in series with data, and level-shift with the 74AHCT125 (3.3 V sits inside WS2812's *undefined* logic band — works on the bench, glitches in the enclosure).

**5 · The memory system will grow without bound and poison everything else.** Extraction, storage and retrieval work today. **Consolidation, contradiction resolution, and forgetting do not** — no framework has a principled decay model. Every system grows monotonically until retrieval degrades, and because the retrieved block enters every prompt, **memory bloat feeds directly back into failure #1**. Your latency will degrade over months in a way you'll misattribute to hardware.

→ Every memory write happens **after** the spoken reply, never in the critical path. Enforce the token budget. Run consolidation on idle with a smaller model than the primary. Steal Graphiti's **bitemporal** idea even without Graphiti — give every fact `valid_at` / `invalid_at`. Don't put Neo4j on a Pi; Kuzu is the only embedded graph backend that makes sense here. And accept there is **no benchmark** for "does this feel like it knows me after two years" — you are flying blind on the one metric that defines the product.

---

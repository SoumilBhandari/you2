# Architecture

## The two clocks

You2 runs on two completely different timescales, and conflating them is the main way projects like this go wrong.

The **hot path** is conversational. It has a hard deadline of roughly one second and it is allowed to be dumb. The **cold path** is memory. It has hours, it runs while you're asleep, and it is where all the interesting work is.

Most people building this spend 90% of their time on the hot path because it's the part you can demo. That's backwards. The hot path is well-trodden plumbing — five open-source components in a row. The cold path is unsolved.

---

## Hot path

### The latency budget is the spec

Human conversational turn-taking has a gap of about 200 ms. Under a second reads as natural. Over two seconds reads as broken, and no amount of answer quality recovers it — this is the single clearest lesson from the AI Pin.

Run sequentially, the stages add up to something like 1.9 seconds, which fails. The fix isn't faster components, it's **overlap**:

| Stage | Starts when | Not when |
|---|---|---|
| ASR | the first audio frame arrives | you stop talking |
| LLM | the transcript is *probably* complete | the transcript is final |
| TTS | the first sentence is generated | generation completes |
| Playout | the first audio chunk exists | synthesis completes |

Done properly this lands near 800 ms with the same components. The engineering is all in the seams.

### Endpointing is the hidden cost

The single largest fixed delay is deciding you've finished speaking. Voice activity detection needs to see silence before it commits, and that wait is pure latency. Too eager and it cuts you off mid-sentence; too patient and every exchange feels sluggish.

Worth knowing up front: this is a tuning problem you will return to many times, and it matters more to perceived quality than model choice does.

### Prefill, not decode, is what will kill you

Every benchmark you will find quotes *decode* tokens per second. That is the wrong metric for this device, and believing it is the single most expensive mistake available here.

You2's whole premise is injecting retrieved memories into every prompt. So the prompt is 500–1500 tokens, not 20. A Raspberry Pi 5 does **prompt evaluation at 38 tok/s** on a 3B model — meaning a 1000-token memory-augmented prompt is roughly **26 seconds before the first token exists**, no matter how fast decode is.

Prefix caching is the mitigation and it's powerful. Structure every prompt as:

```
[ stable persona ] + [ stable memory block ] + [ volatile recent turns + utterance ]
```

with `cache_prompt=true`, and you only prefill the volatile tail — a second or two. But there's a cliff: the moment the memory block changes, which for a memory product is *the entire point*, you take a full cache miss and stall for 8–25 seconds. A product whose response time is bimodal between 1.5 s and 20 s is not shippable.

Three consequences that shape everything else:

**Hard-cap the injected memory block** at a token budget — start around 400 — and enforce it in the retriever rather than hoping. Retrieval quality is a problem to solve *within* that budget, not by injecting more.

**Add an intent router.** A small local classifier that handles timers, weather, "what did I say about X" answers in ~200 ms and never touches the LLM at all. That covers most daily utterances, and it keeps working when the reasoning layer is unreachable.

**Ship v1 hybrid.** Wake word, turn detection, ASR, TTS and the memory store all live on the device. The LLM lives on a machine on your LAN, or behind an API. Define it as one swappable interface — streaming `/v1/chat/completions` — and local inference, a LAN box, an accelerator and a cloud endpoint all become the same code path. The brain can move later without touching the pipeline or the enclosure.

### Never wait for the LLM to finish

A 40-token spoken reply generated at 8.8 tok/s is 4.5 seconds. If you wait for completion before speaking, you have already lost.

**Sentence-chunk the token stream and pipe each completed clause into TTS as it lands.** First audio reaches the speaker in around 600 ms while generation continues underneath. That one decision converts 4.5 s into ~600 ms of *perceived* latency, and it is the thing every "local assistant that feels broken" on the internet skipped.

### Echo cancellation is not optional

The microphones and the speaker share a small plastic box. Without cancellation the device hears itself, transcribes its own speech, and responds to it. Solve this early — retrofitting it after the enclosure is designed is much worse than designing the enclosure around it.

Two mitigations, use both: physical separation and decoupling (mics on the top face, driver forward-firing, soft gaskets so the shell doesn't transmit vibration), and software AEC in the audio stack.

---

## Cold path — the memory system

This is the product. Everything above is a means of getting text in and out of it.

### Why naive RAG fails

The obvious design — embed every conversation, dump it in a vector store, retrieve top-k at query time — works well for about a month and then quietly degrades. Three reasons:

**Retrieval noise grows with the corpus.** More stored fragments means more things that look superficially relevant. Precision drops exactly as the value of having history should be rising.

**Nothing is ever superseded.** You mention forty times over two years that you're working on different projects. All forty are equally retrievable. The system has no notion that thirty-nine of them are historical.

**No sense of importance.** A throwaway remark and a fact that defines how you work are stored identically.

The result is a system that gets *worse* the longer you use it, which is precisely the opposite of the promise.

### The structure that works

Three stores, because different questions need different shapes.

**Episodic** — what was said and when. Append-only, embedded, searchable fuzzily. This is the raw record. "What did I say about the Anderson thing last month?"

**Semantic** — durable facts, with identity. Not `"user said they prefer X"` as a blob, but a record with a stable key that a later statement can *replace*. Each fact carries provenance (which conversation it came from), a confidence, and a validity window. This is what makes supersession possible, and supersession is what stops the store from rotting.

**Graph** — entities and their relationships. People, projects, places, and how they connect. This answers questions embeddings are bad at: "who else is involved in this?" "what am I currently working on?" Structured queries where structure exists; leave the fuzzy stuff to embeddings.

### Consolidation

The highest-leverage component, and the one nobody ships.

The device is plugged in and idle for eight hours a night. Use them. Re-read the day's conversations, and:

- Write a summary of each session, so future retrieval can hit the summary rather than twenty fragments
- Extract candidate facts and either insert them or *supersede* existing ones
- Update the graph with new entities and edges
- Decay or archive episodic material that consolidation has already absorbed

The loose analogy to what sleep does with memory is not accidental, and it's the right intuition: you are trading raw fidelity for structure, and that trade is what keeps retrieval sharp at scale.

### Salience

Something has to decide what's worth keeping, and both failure modes are bad. Remember everything and retrieval drowns. Remember too little and the device feels like it isn't paying attention — which is the one thing it must never feel like.

Start permissive. Keep raw episodic material for a long window and let consolidation do the pruning, rather than throwing things away at capture time when you have the least context about whether they matter.

### Provenance

When You2 tells you something about yourself, it should know where it learned it. This costs almost nothing to carry and it's the difference between a system you trust and one that's confidently wrong at you. It also gives you a repair path: "no, that's out of date" has to be able to find and fix the specific record.

---

## Privacy

Everything above runs locally. That's the point, and it constrains the design in ways worth stating explicitly:

- Raw audio is processed and discarded. It is never stored and never transmitted.
- Wake-word activated, not continuously recording. Other people are in the room and haven't consented to anything. Several US states require all-party consent for recording, and this is a significant part of why the always-listening pendant companies got quietly acquired rather than scaled.
- The memory store is a local file. It should be trivially exportable and trivially deletable, and the user should be able to read it in a text editor.

If the LLM is offloaded to a machine on your LAN for speed, that's still your hardware — but be honest in the docs about where the boundary is, and make it visible in the UI.

---

## What's deliberately not here

**Actions.** Booking, sending, controlling. This is where Rabbit died — the action layer was the entire promise and it was brittle scripting underneath. Conversation and memory need to be genuinely good before anything gets the ability to do things on your behalf.

**A general assistant.** You2 should be worse than a frontier model at trivia and better than any of them at you. Competing on general capability is a fight against the supercomputer already in your pocket.

**Engagement mechanics.** No feed, no streaks, no notifications. It speaks when spoken to.

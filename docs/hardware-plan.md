# Hardware plan — rev D

*Written 9 August 2026, against rev C of the enclosure and the $248 API BOM.*

The software is the product and it works. The hardware is a design document
that has never met a caliper, and this is the pass that treats it as an
engineering problem rather than a costing exercise.

Seven independent audits were run over `cad/you2.scad`, `docs/design.md`,
`docs/bom.md` and the software — CAD geometry, acoustics, thermal, power,
electrical, sourcing, DFM, and ruggedization — and every finding was then given
to a separate agent whose job was to refute it. 3 were confirmed outright, 54
were sharpened, 29 were thrown out. What follows is what survived, plus the
enclosure rewrite it forced.

**One caveat on that method, because it matters when you read the numbers.**
The rewrite was happening *while* the refutation pass ran, so a large share of
those 29 rejections say "already fixed in rev D" or "the repo already documents
this" rather than "not a real problem" — which is circular, and one verifier
said so in as many words. Treat the rejection count as evidence about rev D,
not as evidence that rev C was fine. Four rejections *were* substantive and
have been applied: the drop-energy figure below, the ERLE claim in §7, a panel
revision warning that turned out to belong to a different Waveshare product,
and an over-absolute claim about tapped plastic.

**The short version: rev C cannot be assembled, and three of its parts are the
wrong shape.** Not "needs tolerancing" — the shell exports as two disconnected
solids, its floor is sealed by a 0.9 mm membrane, and half its internal pockets
are cut in empty air and remove no material.

None of that is a criticism of the reasoning in `design.md`, which is mostly
right and in several places better than what I would have written. It is what
happens when a model renders cleanly and nobody checks that "manifold" and
"one object you can build" are different claims.

---

## 1. What was actually wrong

Ranked by what it costs you. Every number here was reproduced independently.

### It cannot be assembled

| | |
|---|---|
| **The shell has no bottom opening.** | `plan_mask` centres its extrusion, so the cut spans z = −3.5…+1.5 while the cavity floor is at z = 2.4. A **0.9 mm PETG membrane seals the entire 129 × 120 mm footprint.** The shell is a closed box. |
| **The shell is two disconnected solids.** | Internal features are clipped with `body_solid(shrink = wall + 0.01)` — a solid 0.01 mm *smaller* than the cavity, not larger. The speaker chamber and the display frame each float free. `cad/check.py` reproduces it: 2 solids, the orphan centred at (0, −51, 29), which is the chamber. |
| **The display cannot be installed.** | A 115 mm carrier has to reach the *inside* of the raked face through the base opening. Nothing retains it if it gets there — `disp_recess` is declared and never referenced by any module. |
| **Nothing holds the shell closed.** | Zero bosses, inserts or clips in 382 lines. The base "rebate" is the same plan section as the cavity, so there is not even a shoulder to seat against. |
| **Three pockets cut nothing.** | The mic PCB recess floats 2.4 mm below the ceiling; the LED stick pocket starts exactly where the wall ends; the driver pocket is in open cavity. |

### Three parts are the wrong shape

- **The speaker is a ~50 × 45 × 22 mm rectangular enclosed box**, not the Ø44 × 18 cylinder modelled. It does not fit the chamber, and no chamber for it fits a 46 mm skirt.
- **The mic pitch circle is ~44 mm, not 58 mm.** 58 appears to be the array's *outline*. Every port misses every capsule, and the array then hears the cavity instead of the room. Two auditors found this independently. **This is the single highest-risk number in the model.**
- **The Pi's USB-A and Ethernet are on a 56 mm short edge**, not a long one. rev C points all of them at a side wall with no opening — and the hole pattern is not centred on the board, so the standoffs are 10 mm from where `proxy_pi` draws them.

### The geometry that was measured wrong

`face_margin` uses the raked slope, 125.12 mm. After the R9 fillets the **flat
face is 117.16 mm** — the echoed "5.06 mm at each end" is really 1.08 mm. The
corner rounding is also a no-op: `plan_mask` is handed `depth * 2`, so its R9
corners sit 60 mm outside the body and the four vertical edges are sharp.

### And the two software defects that are hardware's fault

Both verified in the repo, both one-line:

- **`PRAGMA synchronous = NORMAL` with WAL.** On an always-on mains device with no battery, SQLite's own docs are explicit that a *committed* transaction can roll back after a power cut. `design.md §5.6` already puts every write after the spoken reply, so `FULL` is off the hot path by construction and costs one fsync.
- **The nightly backup writes one file, called `brain-%i.db`.** `deploy/install.sh:158` uses `%%i` in a unit that is not a template, so systemd resolves it to a literal `%i`. There is exactly one backup generation, it is overwritten every morning, and it lives on the same microSD as the database it protects. A corrupt DB becomes a corrupt backup within 24 hours.

---

## 2. What "tough" actually means here

The interesting finding is that the obvious intuition is wrong.

**It is not tippy.** Forward tip is 33°, rearward 37°, sideways 38°. IEC
62368-1 uses 10° for benchtop equipment. The 276 g shell plus the Pi low down
keep the centre of mass at 37 % of the height even with the display high on the
rake. **Do not add mass for stability** — it buys nothing.

**The real cable-tug failure is sliding, not tipping.** rev C slides at ~3.7 N
and can be lifted bodily by its own power cable at ~6.2 N, both far below the
8–20 N retention of a USB-C plug and far below the 22 N needed to tip it.

**And a drop is unsurvivable by stiffness.** A 750 mm desk sweep loads a 2.4 mm
PETG corner to roughly **9–15× what it takes before cracking**; even a 100 mm
topple onto the desk is 3–6× over. Wall thickness does not close that at any
affordable cost. Only a compliant element with real travel does.

*(An earlier draft of this file said 86–220×. That came from comparing energies
when stress governs, and overstates the ratio by about 10×. The conclusion is
unchanged — it still breaks, by a lot — but the number was wrong.)*

So toughness here is three things, in this order:

1. **Do not get knocked off** — friction and mass at the base, and a cable that pulls on the shell rather than on a surface-mount jack.
2. **Fail cheaply** — the display is $87 of the build and the likely casualty. It must be replaceable without touching anything else.
3. **Survive the boring stuff** — the spill, the dust, the three years at 45 °C, the SD card, the backlight. These are what actually kill desk objects, and none of them are dramatic.

### One disagreement, and how it resolved

The ruggedization audit says *do not add ballast*: it raises drop energy 49 %
for no stability benefit. That is right about stability and I have kept its
conclusion — but its own analysis identifies **sliding and lifting** as the real
failure mode, and both scale directly with mass. That reasoning survived, but the **part did not**: an aluminium plate with a
rounded outline, a slot field, an SD window, four counterbores and four
standoff bosses is a milled part, and the build has to run on one 3D printer
and nothing else. So the base is printed PETG with heat-set inserts, and the
mass comes instead from **two sand-filled ballast pods** in the dead volume
either side of the Pi — 63 cm³ each, ~200 g of sand or ~570 g of steel shot,
no machining, and lower down than the plate ever was.

---

## 3. rev D

`cad/you2-revD.scad` — parametric, self-sizing, and it asserts its own
buildability. Two gates run over it: `cad/check.py` proves every part is a
single connected solid, and `cad/fit.py` proves no two parts occupy the same
space. Between them they caught nine defects that renders and asserts both
missed, including a driver that never touched its own seal and a microphone
array that overhung the front edge of the top face.

**The complete build document — every part, plate, fastener and step — is
[`docs/build.html`](build.html).**

### The one architectural change

**The raked face is now a separate faceplate, and the display never enters the
shell.** That single move fixes the assembly impossibility, makes the
expensive part serviceable, gives the best print surface to the visible face,
and means every dimensional error about the display costs a 4 h reprint of a
56 g part instead of a 30 h reprint of a 430 g one.

### The box is now sized *by* the parts

rev C fixed the form and discovered the parts did not fit. rev D derives the
form:

```
spk_z   = base_t + 4 + seal_w + spk_by/2
skirt_h = ceil(spk_z + spk_by/2 + seal_w + 4)
H       = ceil(skirt_h + (disp_carrier + bar_allow + 4 + 2*fp_lip_y
                          + t_top + t_sh) * cos(rake))
```

Change a part dimension and the enclosure re-sizes. Four asserts fail loudly
if a measurement makes it unbuildable.

**152 × 150 × 189 mm** (203 mm tall with the light bar). Bigger than rev C's
134 × 125 × 165 — which was never a size the parts fitted in.

#### Where the height goes

| | mm |
|---|---|
| base plate + floor clearance | 8 |
| driver box (50 × 45 × 22) + two gasket lands | 53 |
| shoulder | 4 |
| display carrier, up the face | 107 |
| fastener flanges + corner fillets | 17 |
| **total** | **189** |

Two levers, if this is too big: a driver box 20 mm shorter takes it to 169 mm;
a smaller display carrier takes it to 162 mm. **The display and the driver are
the whole object.** Everything else is 8 % of the height.

### The rest of the changes

| | rev C | rev D |
|---|---|---|
| Parts | 1 printed shell + 1 plate | 11 printed parts: shell, faceplate, base, 2 clamps, 2 ballast pods + lids, 3 gaskets, foot |
| Walls | 2.4 mm, no ribs | 3.2 mm, 4.5 mm face, side fins + rear hoops |
| Rake | 18° | 22° (a seated eye at 600 mm is on a 25° sight line) |
| Corner fillets | R9, and a no-op in plan | R6 profile / R8 plan, both real |
| Display | Ø87.6 bore, zero clearance, unretained | bolted to the faceplate on its own hole pattern, recessed 2 mm behind the bezel, on a 1 mm gasket |
| Speaker | Ø44 cylinder in an open-ended "sealed" tube | rectangular box on a gasket land, clamped, decoupled |
| Mics | pocket that cuts nothing, ports on a 58 mm circle | 44 mm circle, four posts pulling the array onto a 1.5 mm TPU gasket with a hard stop at 20 % compression |
| Fasteners | one tapped-M3-into-PETG feature | 24 heat-set inserts, no tapped plastic anywhere |
| Power | 2 mains plugs | 1, straight through a grommet into a clamped saddle |
| Print | "face down, no support" — 1.63 L of trapped support | **top face down**, no horizontal ceilings |

### Toughness spec

| Threat | Measure |
|---|---|
| Drop / sweep | TPU 95A shoe wrapping the bottom 8 mm, 3 mm wall, corner bumpers concentric with the plan radii, gyroid infill. ~47 g. Cuts peak force ~3×; more importantly it is the part that fails. |
| Slide / cable tug | Aluminium base (~190 g) + 60A silicone feet (μ 1.2–1.5 on wood). Panel-mount USB-C in the rear wall; the internal clamp grips the jacket so the Pi's jack never sees a load. |
| Glass | Recessed 2 mm behind the printed bezel — the bezel is always the first thing to touch anything. 1 mm silicone under the panel flange, so it is never point-loaded by printed plastic. |
| Spill / dust | **Target IP52.** **GORE GAW3343.06.0** ePTFE acoustic vents (ID 3.0 / OD 6.0, 0.31 mm) bonded into the counterbores over the four mic ports: < 2 dB at 1 kHz, IP68 in themselves, ~$0.50 each. *(An earlier draft named GAW331 — that series does not exist in Gore's catalogue.)* Mesh behind the speaker grille. Solid diffuser sealing the light-bar slot if fitted. The shoe drains rather than holding a spill. |
| Thermal creep | Heat-set inserts (M3 4.6 × 5.8, M2.5 4.0 × 4.0), boss OD ≥ 7.0 mm, 0.4 N·m and 0.25 N·m. **One exception**, named rather than hidden: the four mic posts still self-tap M2.5 into PETG, because a Ø66 pitch circle on a Ø73 PCB has no room for a Ø7.5 boss. Acceptable there because the load is a compressed elastomer, the screws bottom on a hard stop so preload is bounded by geometry, and the top of the box is the coolest part of it. |
| Backlight wear | The real ageing mechanism is **not** burn-in — it is an IPS panel. It is backlight L70 at **2.3–4.4 years** of 24/7 full drive. Blank after 3 min idle and wake on the wake word; run the idle clock at 15–20 %. Roughly 6× the life, in software. |
| SD wear + corruption | `synchronous = FULL`, a rotating backup with `PRAGMA integrity_check`, and **at least one generation on a second device**. |
| Repair | Display: 4 screws, from the front, without opening the box. Everything else: 4 screws from below. Ten screws total. |

---

## 4. What it really costs

`$248` is not a real number. Corrected and completed:

| | |
|---|---|
| Core electronics (Pi 2 GB, display, FFC, **Flex 4-mic**, PSU, SD, heatsink, driver) | **$239** |
| Mechanical (filament ×2 spools, inserts, fasteners, gaskets, membranes, grommet) | **$100** |
| Off-card backup stick | **$6** |
| **Total, no light bar** | **≈ $339** |
| with the light bar (sticks, shifter, cap, fuse, diffuser) | ≈ $357 |

Plus shipping from three or four vendors — realistically **≈ $400 delivered**.

The biggest single correction is the mic array: the BOM buys a **2-mic
rectangular reSpeaker Lite at $25** while the CAD, the README and `design.md`
all describe a **4-mic 73 mm round Flex at $55**. Those are not interchangeable
and the enclosure only works with one of them.

Deleted: the second 5 V supply. Measured against the shipped palette in
`software/you2/runtime/leds.py` (`MAX = 0.30`, 16 pixels), worst case is
**0.19 A** — the rule "never off the Pi's 5 V pin" was written against a
24-LED ring at 1.3 A. One supply, one 500 mA polyfuse, and the failure mode
becomes "the light bar goes dark" instead of "the Pi reboots mid-sentence".

---

## 5. Should the light bar exist?

It costs **14 mm of height and 8 mm of depth**, and it is the only soldering,
the only level shifter, the only second supply, and a 459 mm² unsealed aperture
in the front face 3 mm above the driver — 2.3× the speaker grille's open area.

Against that: an 800 × 800 emissive disc showing a full-field colour is *more*
visible across a room than a 4.5 mm diffused slot, and it is already there.

`LED_BAR = false` is the default in rev D. It is one parameter, the model
re-sizes, and both variants are checked. **Build v1 without it**, and add it
later as a module if the screen genuinely does not carry state well enough. The
argument in `design.md §7.5` for a bar over a ring is completely right; it just
never asked whether the bar should exist at all.

---

## 6. Build sequence, ordered by information gain

**Nothing here needs the enclosure.** The software already runs with no
hardware; the point of the first three steps is to kill the design before you
print anything.

**Order now, because everything depends on them and lead times are long:**
reSpeaker Flex XVF3800 Circular-4, the display *with the panel revision
confirmed*, the 22-pin FFC, Pi 5 2 GB, the official 27 W PSU, the driver.

| | Experiment | Kills |
|---|---|---|
| 1 | **Barge-in**, on a bench, parts loose. Long TTS at normal volume; wake word over the top at 2 m and 4 m. | The whole product. Do this first; it is an afternoon. |
| 2 | **Moonshine streaming C++** with cached encoder state, on the Pi. | The board choice. The Python one-shot API measures 3 s where the streaming library measures 237 ms, and that 12× gap will send you Jetson-shopping for no reason. |
| 3 | **Calipers on all seven dimensions** in `hardware/measure-first.md`, `mic_circle_d` first. | The enclosure. Change the parameters, re-run `cad/check.py`. |
| 4 | Continuous power draw, idle, screen on, with an inline meter. | The passive-cooling argument, which was only ever made about the CPU. |
| 5 | `you2 extract --live` (one command, one key). | Nothing physical — but it is the highest-value hour in the project and it needs no hardware at all. |
| 6 | Print the **faceplate only** and offer up the display. | Four of the seven dimensions, for 56 g and 4 h. |
| 7 | Print the shell. | — |

**Do not print the shell until step 6 passes.** It is ~430 g and ~30 h at
0.2 mm; use a 0.6 mm nozzle at 0.28 mm and it is closer to 12 h.

---

## 7. What is still unknown

Honestly, and with the measurement for each:

- **Everything acoustic.** Whether XMOS AEC holds with this coupling, and whether the top panel's fundamental sits on the driver's Fo. → Step 1, then repeat inside the box, because the box is what couples them.
- **Which speaker.** The $248 BOM lists "small 4 Ω 3 W speaker, $6" with **no part number**, while the CAD and the wiring doc assume the 5 W Seeed 114993346. No target playback SPL is recorded anywhere, so the echo budget cannot be computed before ordering. → Pick the part, write down the SPL you intend to play at. *(An earlier draft claimed the driver's own distortion caps ERLE at ~20 dB, below what barge-in needs. That does not hold: the datasheet's "10 % max" carries no test condition and is a rated-power limit, and at this device's actual ~16 mW operating point the nonlinear echo floor is nearer 33–45 dB — comfortably above the ~21 dB a wake-word onset needs.)*
- **Whether the reSpeaker is 16 kHz-capped over USB.** If it is, the 4-mic upgrade buys beamforming, not bandwidth, and TTS quality is capped whatever voice you pick. → Read the USB audio descriptor.
- **Whether the speaker output is bridged.** The arithmetic says yes. If yes, **neither lead may be grounded** and getting it wrong destroys the amplifier. → Datasheet, before connecting anything.
- **Real interior temperature at 60 mm rear clearance.** All venting is on the rear face; pushed against a wall the box is sealed. → An hour of `vcgencmd get_throttled`.
- **Whether `mic_circle_d` is 44.** → Calipers. Nothing else in the build is as cheap to check or as expensive to get wrong.

---

*Enclosure: [`cad/you2-revD.scad`](../cad/you2-revD.scad) · gate:
[`cad/check.py`](../cad/check.py) · renders:
[`cad/renders/revD/`](../cad/renders/revD/) · wiring, assembly and the
measurement list: [`hardware/`](../hardware/)*

# Hardware handoff

*The physical half, in detail. Written 9 August 2026, current as of 10 August.*

[HANDOFF.md](HANDOFF.md) is the whole project — repo map, run commands, the
standing traps. **Read that first.** This is the companion for whoever picks up
the object specifically, alongside [handoff_software.md](handoff_software.md)
for `software/`.

**Nothing here has been built.** Nothing printed, no part ordered, no dimension
measured with a caliper.

All of it is committed and pushed — `3052567` carried the model, the gates, the
renders and `hardware/`; `2d847d9` rebuilt the README's hardware section around
rev D; `7358248` corrected the fastener count and the membrane part number
across the hardware docs. The working tree should be clean; if it is not,
something is in flight.

What it *has* been through is a design pass that treats it as an engineering
problem rather than a costing exercise, and two mechanical gates that catch the
class of defect a render cannot show.

The one-line version:

> rev C could not be built. rev D can — on one ordinary printer, with a
> soldering iron and a box of screws — and it says so with asserts rather than
> with prose.

---

## Where to start

| | |
|---|---|
| **[`docs/build.html`](docs/build.html)** | **The build document.** Every part, all six print plates, the complete parts list, the fastener schedule, twelve assembly steps, wiring, costs. Open it in a browser. Start here. |
| [`hardware/measure-first.md`](hardware/measure-first.md) | The seven numbers to check before spending money. Read it before ordering, not before printing. |
| [`docs/hardware-plan.md`](docs/hardware-plan.md) | Why rev C failed and what rev D does about it. The reasoning, not the instructions. |
| [`cad/you2-revD.scad`](cad/you2-revD.scad) | The model. Parametric and self-sizing. |
| [`hardware/wiring.md`](hardware/wiring.md) · [`hardware/assembly.md`](hardware/assembly.md) | Circuits and grounding · the short-form build order |
| [`README.md`](README.md) | the hardware section is the two-minute version of all of the above |

**The STLs are not in git** — `.gitignore` has `*.stl`, and they regenerate from
the model in one command, so there is nothing to go stale:

```bash
mkdir -p cad/stl/revD && for m in shell faceplate base mic_clamp driver_clamp \
  ballast_pod ballast_lid mic_gasket panel_gasket speaker_gasket foot; do
  openscad -o cad/stl/revD/you2-$m.stl --export-format=binstl \
    -D "MODE=\"$m\"" cad/you2-revD.scad
done
```

**rev C is gone.** `cad/you2.scad`, its eight renders, its two STLs and the
general-arrangement SVG were deleted, and `cad/README.md` — which documented
that file, down to an 18° face angle and a six-entry MODE table where rev D has
sixteen — was rewritten around rev D. It is in git history if you want to read
how the form was arrived at. Nothing in the working tree describes a model you
cannot build any more, which was not true before.

---

## The object

**152 × 150 × 192 mm.** A 22° raked face carrying an 87.6 mm round display; a
vertical skirt below it carrying a forward-firing driver; four microphones on
the top surface, as far from that driver as the box allows. All venting on the
rear face, because a vent on the front or top is a leak path from the speaker
straight into the microphones.

**Almost nothing about the size was chosen.** The display carrier sets the face
length and therefore the height; the driver box sets the skirt. Both genuinely
re-derive — change `disp_carrier` to 130 and H moves 192 → 206 on its own.

Width and depth are the two still picked by hand (`W = 152`, `D = 150`). The mic
array and the fastener flange do not derive them, they **assert against** them:
raise `mic_pcb_d` and the model does not grow, it stops with *"top face too
shallow for the array — raise D or drop the rake"*. That is the intended
behaviour, but it is a guard rail, not a derivation.

### Printed parts

| part | qty | material | plate | mass |
|---|---|---|---|---|
| `shell` | 1 | PETG | 1 | 400 g |
| `base` | 1 | PETG | 2 | 92 g |
| `driver_clamp` | 1 | PETG | 2 | 5 g |
| `faceplate` | 1 | PETG | 3 | 56 g |
| `mic_clamp` | 1 | PETG | 3 | 11 g |
| `ballast_pod` | 2 | PETG | 4 | 24 g each |
| `ballast_lid` | 2 | PETG | 4 | 4 g each |
| `mic_gasket` | 1 | TPU 95A | 5 | 7 g |
| `panel_gasket` | 1 | TPU 95A | 5 | 1 g |
| `speaker_gasket` | 1 | TPU 95A | 5 | 1 g |
| `foot` | 1 | TPU 95A | 6 | 45 g |

**730 g** total — 676 g PETG, 54 g TPU. Six plates, every one fitting a
220 × 220 × 250 bed. Plates 4 and 6 are optional.

24 brass heat-set inserts, 24 screws, 4 standoffs. **≈ $351 in parts.**

---

## Running the gates

Do this after any parameter change, before any print.

```bash
python3 cad/check.py cad/you2-revD.scad     # every part is ONE connected solid
python3 cad/fit.py   cad/you2-revD.scad     # no two parts share space
openscad -o /dev/null --export-format=binstl -D 'MODE="shell"' cad/you2-revD.scad
```

All three gates pass on both variants (`-D LED_BAR=true` as well), and 16 asserts
inside the model pass.

Render a PNG with `--render`. **Never preview** — the CSG tree exceeds
OpenSCAD's preview normaliser and silently produces a blank image, which cost
me an hour before I noticed.

---

## Things that will bite you

### 1. `Status: NoError` is not a buildability claim

It means the mesh is manifold. It does not mean the part is one object, and it
does not mean anything fits. rev C rendered clean, exported clean, and was a
sealed box in two disconnected pieces. That is why `check.py` exists, and it
caught two of my own regressions while I was writing rev D.

### 2. Neither does a render

A render draws parts in different colours and lets them pass straight through
one another. `fit.py` found four things renders did not: the microphone array
overhanging the front edge of the top face by 2 mm, the mic clamp bosses
sitting inside the clamp ring, the ballast pods fouling the cable clamp, and
the side ribs running down into the base plate's own space.

### 3. And neither gate can see a stack-up that does not close

Nothing overlaps when a gasket floats — it just floats. Two of these were wrong
by less than a millimetre: the display posts left the 1.0 mm panel gasket
0.19 mm to live in, and the driver clamp had exactly zero preload travel. Both
now have an assert, as does the mic gasket. They check a **5–45 % band** rather
than 20 % exactly — 20 % is what the numbers land on, not what is enforced.
**If you change a gasket thickness, the assert is the thing that keeps you
honest;** the mic gasket only got one on 10 August, and writing it immediately
caught that `mic_squash` is the *clamped height*, not the compression.

### 4. A feature intersected with `body(shrink = wall + eps)` floats free

`shrink = wall + 0.01` is a solid *smaller* than the cavity, so anything
clipped with it sits 0.01 mm clear of the wall it was meant to fuse to. The
file uses `INNER = wall - 0.8` so features overlap the wall by 0.8 mm — and
that is now a real expression. It was a literal `2.2` with the derivation only
in a comment, which would have silently stopped tracking `wall`. This is
the single most repeatable mistake in the model and it is invisible until
`check.py` runs.

### 5. Every insert hole is bored *smaller* than the insert

Ø4.0 for a 4.6 mm M3, Ø3.6 for a 4.0 mm M3, Ø3.2 for a 3.5 mm M2.5. Bore at
the insert's own diameter — which an earlier draft did, at eight locations —
and there is zero interference: it spins out the first time you torque a screw.

### 6. Print orientation is load-bearing, and "no supports" needs measuring

The shell prints **top face down**. In that orientation the bottom opening
faces up so the cavity has no ceiling, the raked face and skirt are steep
self-supporting walls, and the mic ports print crisp against the plate.

Measured in the model's own plate-1 orientation: **141.6 cm² of bed contact and
31.2 cm² facing downward at more than 60°.** The largest single contribution is
7.2 cm² of 1.2 mm shadow-gap ledge, which bridges; the next largest is 2.9 cm²,
only seven patches exceed 1 cm² at all, and 6.6 cm² of the total lands in the
first 20 mm of the print. **No large flat
unsupported span anywhere** — which is the thing that actually fails.

Two features had to be rebuilt to get there. The driver bosses were Ø10 × 24 mm
*horizontal cantilevers* whose first printed layer was a 24 mm strip anchored
at one end, needing support towers from the bare bed inside a sealed cavity;
they are now webs with a 45° ramp. The cable saddle opened the wrong way and
its floor was a 216 mm² bridge.

### 7. The display never enters the shell

It bolts to the faceplate, from outside, and the faceplate goes on last. This
is the whole reason the face is a separate part — rev C had no way to install
the display at all. It also means a wrong display dimension costs a 56 g
reprint instead of a 400 g one. **Print the faceplate first and offer the real
panel up to it.**

### 8. The bezel overlap *is* the retention

`(disp_panel_d - aperture) / 2`. Nothing else holds the glass in. If the panel
measures smaller than the assumed 91.5 mm, that overlap goes to zero and the
display falls out of the front of the machine.

---

## The one that can invalidate the design

**`mic_circle_d`.** Seeed describes the array as *"44 mm spacing arranged in a
circular layout"*. If that is the pitch-circle diameter, the design works as
drawn. If it means the distance between *adjacent* capsules — which is what
"spacing" means on the linear-4 sibling — then the pitch circle is
44 × √2 = **62.2 mm**, and at that value the model refuses to render — for want of
adhesive bond land around each vent, not because the ports leave the board.

Be precise about why, because an earlier draft of this file was not. The ports
do **not** fall off the board: at 62.2 the counterbore's outer edge sits 2.3 mm
inside a Ø73 PCB. What it violates is the model's own 4 mm edge keep-out, by
1.7 mm — and 2.3 mm of board edge is not enough to bond a Ø6 membrane onto, let
alone whatever the array carries around its rim.

Still not a tolerance problem, and still a top-face redesign. Just for a
different reason than "it falls off".

```bash
openscad -o /dev/null --export-format=binstl -D 'MODE="shell"' \
  -D 'mic_circle_d=62.2' cad/you2-revD.scad     # fails, with the reason
```

**Resolve this before ordering anything.** Calipers on the real board, or an
email to Seeed. It is the cheapest thing in the project to check and the most
expensive to get wrong.

---

## Honest gaps

**No part has been touched.** Everything below is reasoning, not measurement.

**Everything acoustic is unknown.** Whether the XMOS echo canceller holds with
a driver this close to the array is the single highest risk in the project and
it is untestable until parts exist. Test it on a bench with everything loose,
before any of this is printed: long response at normal volume, wake word over
the top from 2 m and from 4 m. If it fails, nothing else here matters.

**Which speaker, exactly.** There is a candidate — Seeed 114993346 — and the
model is dimensioned around it. What is missing is a decision: every costing
that named a part number has now been deleted for buying the wrong microphone
board, `build.html` carries the part but no target playback level is written
down anywhere, so the echo budget still cannot be computed before ordering. The model assumes a 50 × 45 × 22 mm enclosed box, and its height sets
a third of the object's height.

**Whether the reSpeaker is 16 kHz-capped over USB.** If it is, the four-mic
upgrade buys beamforming rather than bandwidth, and voice quality is capped
whatever you pick.

**Real interior temperature.** All venting is rear-only and the device needs
≥ 60 mm of clearance behind it. Pushed against a wall the box is sealed. An
hour of `vcgencmd get_throttled` settles it.

**Whether the speaker output is bridged.** The arithmetic says yes. If so,
**neither lead may be grounded** and getting it wrong destroys the amplifier.

**The reSpeaker core board has no proper home.** The base has cable-tie slots
for it, which is adequate for a 20 g board but is not a mount. If it moves
around in practice, it needs bosses.

**Two of the three stale documents are fixed.** `cad/README.md` was rewritten
around rev D. `docs/bom.md` had all five of its costings deleted — every one of
them bought the two-microphone Lite for a four-microphone enclosure — and what
remains is the analysis they were wrapped around. `docs/design.md` §7 still
describes rev C's form, deliberately: the argument survived the revision and
the numbers did not, and it now says which is which at the top of the section.

`docs/hardware-plan.md` is only *mostly* rev D — its toughness table still
specifies the aluminium base plate the same document abandons a few lines
earlier. Read it for the reasoning, not for the parts.

---

## What was found, and how

Two audit passes, each fanned out across independent reviewers and then given
to separate agents whose only job was to refute the findings.

- **Pass 1** — rev C, across CAD geometry, acoustics, thermal, power,
  electrical, sourcing, DFM and ruggedization. Established that rev C could not
  be assembled and forced the rewrite.
- **Pass 2** — rev D, across printability, assembly, fit, completeness,
  thermal-acoustic and industrial design. 5 confirmed, 29 sharpened, 22 refuted.

Those verdict counts are **not checkable from this repository** — they lived in
the run that produced them and nothing here records them. Treat them as
provenance, not evidence. What *is* checkable is the geometry, and that is what
the gates are for.

**Twenty-three defects that renders did not show.** The gates are the durable
part of that: `check.py` and `fit.py` will catch the same classes again.

Two cautions when reading those numbers. A share of the refutations mean
"already fixed mid-flight" rather than "not real" — I was editing the model
while the auditors read it, and one verifier flagged the circularity itself.
And four claims that reached these documents were wrong and have been
corrected in place: a panel-revision warning that belonged to a different
Waveshare product, a drop-energy figure overstated tenfold by comparing
energies where stress governs, a claim that driver distortion caps echo
cancellation, and an acoustic membrane part number (GAW331) that does not
exist — the real part is **GORE GAW3343.06.0**.

---

## Suggested next work

Ordered by information gain. Nothing in the first four steps needs the
enclosure to exist.

1. **Resolve `mic_circle_d`.** An email or a caliper. It gates the top face.
2. **Barge-in on a bench**, parts loose. It gates the product.
3. **Moonshine streaming C++** with cached encoder state on the Pi. It gates
   the board choice — the Python one-shot API measures 3 s where the streaming
   library measures 237 ms, and that gap sends people board-shopping for
   nothing.
4. **Calipers on the other six dimensions**, then re-run both gates.
5. **Print the faceplate only** and offer the display up to it. 56 g, ~5 h,
   and it confirms or destroys four of the seven numbers.
6. Print the shell. ~400 g, ~31 h at 0.2 mm — closer to 15 h with a 0.6 mm
   nozzle, which nothing on this part suffers from.

---

## Conventions

- **The model asserts its own buildability.** If you add a feature that depends
  on a dimension, add the assert too. Fourteen of them exist and each one
  corresponds to a defect that got through once.
- **Run both gates before every print.** They take seconds and they have caught
  more than every render combined.
- **Correct claims in place.** Four are corrected above with the reason kept,
  because a correction you cannot see is one you will make again.
- **Nothing here has been built,** and every document should keep saying so
  until it has.

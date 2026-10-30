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
problem rather than a costing exercise, and four mechanical gates that catch the
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

**676 g** total — 622 g PETG, 54 g TPU. Six plates, every one fitting a
220 × 220 × 250 bed. Plates 4 and 6 are optional.

24 brass heat-set inserts, 24 screws, 4 standoffs. **≈ $351 in parts.**

---

## Running the gates

Do this after any parameter change, before any print.

```bash
python3 cad/check.py cad/you2-revD.scad     # every part is ONE connected solid
python3 cad/fit.py   cad/you2-revD.scad     # no two parts share space
python3 cad/check.py cad/you2-revD.scad --variants   # ...under every switch
python3 cad/thickness.py cad/you2-revD.scad  # no wall a 0.4 nozzle cannot fill
software/.venv/bin/python cad/mass.py cad/you2-revD.scad   # no impossible masses
git config core.hooksPath .githooks          # once: the fast two on every commit
python3 cad/docs.py                         # the prose agrees with the model
python3 cad/render.py                       # regenerate all nineteen images
```

All three pass, under all five switch combinations, and 21 asserts inside the
model pass.

**`--variants` is not optional and this file used to claim its result without
running it.** The model has three switches; only the default was ever gated.
`LED_BAR = true` exported the shell with a 1.7 × 1.2 × 2.0 mm chip floating at
each side — the rev C failure, in a variant three documents called clean. The
cause was width: the bar lengthens the faceplate, which needs depth for the
array *and* width for the side lip, and `D` grew with `LED_BAR` while `W` did
not. Both do now, and `W` is 158 in that variant.

**Each gate proves it can fail before it grades anything.** `check.py` runs its
union-find over a mesh it knows is two pieces; `fit.py` measures an overlap
whose volume is known by arithmetic (two 10 mm cubes offset 5 mm intersect in
exactly 125 mm³); both refuse to run if the control is wrong, and both were
confirmed by sabotage rather than by reading the code. Eleven parts reporting
"1 solid" and fourteen pairs reporting 0.000 is also what a completely broken
instrument prints.

**`cad/docs.py` is the newest and the one that earns its keep fastest.** The
other two read no English, and every defect found in this project since rev D
landed has been a sentence. It derives seventeen facts — overall size, rake,
skirt, assert count, MODE count, part count, plate count, four measured off the
exported mesh in the plate-1 orientation, and filament, fasteners and money
summed from the repo's own tables — then errors on any document that disagrees.
Nothing expected is typed into it. It found 20 wrong claims on its first run and
10 more when it learned to add up tables.

**It also got one wrong, in the dangerous direction.** The filament headline
said 674 g and the parts table summed to 676. The gate cannot tell which side is
wrong, so it reported the disagreement and the headline was "corrected" to 730 —
because the ballast pod and lid rows gave the PAIR mass in a per-piece column,
and multiplying by qty 2 counted them twice. 674 had been right all along.
What caught it was physics, not arithmetic: a printed part cannot weigh more
than the same part solid, and the pod claimed 49 g against 27 g of solid PETG.
`cad/mass.py` now checks exactly that, and no table in this repo can claim an
impossible mass again.

**Never preview.** `cad/render.py` always passes `--render`, because the CSG
tree exceeds OpenSCAD's preview normaliser, which aborts and writes a **blank
PNG with no error and exit 0**. The script size-checks every output for that
reason. It also fixes camera distance explicitly rather than using `--viewall`,
which rescales between revisions and makes two renders impossible to compare.

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
30.5 cm² facing downward at more than 60°.** The largest single contribution is
7.2 cm² of 1.2 mm shadow-gap ledge, which bridges; the next largest is 2.7 cm²,
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

**One number is purely aesthetic, and it is at its ceiling.** `fp_r` — the
corner radius shared by the faceplate and its rebate — was a hard-coded 6, which
made the face a rectangle carrying a circle inside the shell's own rectangle. It
is 16 now. It cannot go higher: the corner screws sit in the corner quadrant and
their Ø6.2 counterbores must stay inside the arc, so 17 leaves 1.15 mm and 18
leaves 0.89 — under three perimeters on a corner under screw preload. If you
want a rounder object the lever is `W`, not `fp_r`.

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

### What a clean sweep looks like

Run 2026-10-28, after the gates above. Recording it because "we looked and found
nothing" is worth exactly as much as the method used, and a future reader
deserves to know which stones were turned.

| swept | result |
|---|---|
| Unused imports | 1 — `Sequence`, live inside a string annotation. Unquoted; not dead. |
| Module-level defs nothing calls | 0. Six looked dead and all six had call sites the filter was hiding by excluding dotted access. |
| Names in `__all__` nothing imports | 1 of 204 — `__version__`, which is API by convention. |
| `meta[...]` keys nothing reads | 13, none of them dead logic; each sits beside the `invalid_at` or confidence cut that does the work. They are provenance, and nothing could display them. `you2 inspect` now can. |
| `BrainConfig` knobs nothing reads | 0, down from 5. The remaining gap, `barge_in`, is listed in the test's own `UNWIRED_CONFIG`. |
| `except: pass` that swallows | 8, all typed, all commented, all deliberate — broken pipes, queue races, signal registration off the main thread, an LLM fallback to rules. |
| Bare `except:` | 0 |
| Mutable default arguments | 0 |
| Self-comparisons, repeated boolean operands | 0 |
| CAD: uncalled modules, unused variables | 0 |
| `deploy/` paths and shell syntax | all resolve, both scripts parse |

The dead code is gone. What the sweeps keep turning up instead is the shape this
project actually fails in: something written and never wired to the thing that
was supposed to consume it.

### And what to build, as opposed to measure

The gates have now caught more than the renders ever did, and each new one has
paid for itself on its first run. That is the pattern worth continuing.

1. **Put a slicer in the gate.** Print time is now the last number in this
   project maintained entirely by hand. Mass is half-derived: `cad/mass.py`
   computes each part's solid volume from its mesh and rejects any claimed mass
   above it, which is a hard bound rather than an estimate, and it caught a
   ballast pod claiming 1.82x its own solid mass. What it cannot do is predict
   the infill, so the claimed figures are still typed. PrusaSlicer and
   OrcaSlicer both have a CLI that produces mass and time from an STL; neither
   is installed here, and a gate that cannot be run is not worth writing
   blind.
2. **A minimum-feature check.** Nothing here measures wall thickness. A part
   can be one connected solid, clear of every other part, satisfy all
   twenty-one asserts, and still have a 0.3 mm web that no slicer will produce.
   It is the classic FDM failure and this repo is currently blind to it.
3. ~~**Run the gates on commit.**~~ Done — `.githooks/pre-commit`, installed
   with `git config core.hooksPath .githooks`. `docs.py --quick` (0.1 s) on
   every commit, the two reachability tests (1.0 s) when `software/` changes,
   and `check.py`, `fit.py` and `mass.py` (8 s together) when `cad/` does. The
   full suite stays out at 112 s, and so does `thickness.py` at 26 s.

   Those last three were excluded for months on the grounds that the mesh
   gates were "minutes". That was a measurement of Ubuntu's OpenSCAD 2021.01,
   whose booleans go through CGAL Nef polyhedra, and nobody re-took it after
   Manifold became the default — `fit.py` is 5 s on a current build and was
   still running after 4 h 20 m on the old one in CI. The hook checks the
   OpenSCAD version and falls back to printing the commands if it finds one
   older than 2023, because on that build it really would take hours.
4. **Derive `W` and `D`.** Attempted and deliberately not done; the model now
   says why where the constants are. The flange assert wants `W > 151`, but the
   real threshold is `W = 154` for `LED_BAR = true` and `152` for the default —
   swept with `check.py`, 152 and 153 chip. Nothing in the x-direction differs
   between the variants, so the bound moves through `fp_l` → `H`, `D`, rake,
   sliding the rebate's corner along a surface rolled in plan and filleted in
   profile. Deriving that means re-deriving the rolled corner. A formula that is
   subtly wrong is worse than a constant with a gate on it.
5. **Decide the light bar.** It builds now, but it is off by default, costs
   6 mm of width, 8 mm of depth and 15 mm of height, and is the only soldering,
   the only level shifter and the only unsealed aperture in the project. Commit
   to it or delete it; maintaining an off-by-default variant is how it broke.
6. **The rear I/O bezel and the reSpeaker mount**, both already named in the
   model as deferred until a board can be measured. Small prints, and neither
   can be designed from a datasheet.
7. **`docs/build.html` has no generator.** It is the best thing in this repo and
   it is hand-edited, because the script that made it lived in a scratch
   directory that no longer exists — the same way the renders lost their source
   until `cad/render.py`. Its tables are now the authority for mass and money,
   so it cannot be regenerated from the model until (1) lands.

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

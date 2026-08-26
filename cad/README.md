# CAD

`you2-revD.scad` — the enclosure. One file, parametric, and **self-sizing**: the
box is derived from the parts that go in it, not drawn to a shape and then
checked. Change `disp_carrier` at the top and the height, the raked face, the
flange and the print plates all move. Needs [OpenSCAD](https://openscad.org)
2021.01 or newer.

```
152 × 150 × 192 mm   ·   11 printed parts   ·   674 g   ·   6 plates
```

Everything below is rev D. **rev C is gone** — it was deleted in the commit that
rewrote this file, and it is in git history if you want to read how the form was
arrived at. It could not be built: sealed floor, two disconnected solids,
nothing retaining the display and nothing fastening the shell shut.

## Render it with `--render`. Never preview.

This is the one operational rule that will waste your afternoon. The CSG tree
exceeds OpenSCAD's preview normaliser, which **aborts and silently writes a
blank PNG** — no error, no warning, an empty image that looks like a camera
problem.

```bash
openscad -o hero.png --render --imgsize=1400,1100 --viewall --autocenter \
  --camera=0,0,0,62,0,28,0 --colorscheme="Tomorrow Night" \
  -D 'MODE="exploded"' cad/you2-revD.scad
```

## The gates

`Status: NoError` means the mesh is manifold. It does **not** mean the part is
one object, and it does not mean anything fits. Both of those failures render
beautifully.

```bash
python3 cad/check.py cad/you2-revD.scad   # every part is ONE connected solid
python3 cad/fit.py   cad/you2-revD.scad   # no two parts occupy the same space
python3 cad/docs.py                       # the prose agrees with the model
```

The first two begin by proving they can fail — `check.py` on a mesh it knows is
two pieces, `fit.py` on an overlap of known volume. A gate that has only ever
passed is not evidence.

The third one exists because neither of the others reads English, and every
defect found since rev D landed has been a sentence. It derives the numbers from
the model and errors on any document that disagrees.

Run all three before every print, and **add an assert whenever you add a feature
that depends on a dimension**. There are 16 in the model now; between them and
the two geometry gates they caught 23 defects that renders did not show.

## MODE

Sixteen entries. Set it at the top of the file or pass `-D 'MODE="shell"'`.

| | |
|---|---|
| `exploded` `assembly` `section` | the whole device — start at `exploded` |
| `shell` `faceplate` `base` | the three structural prints |
| `mic_clamp` `driver_clamp` | the two clamps |
| `ballast_pod` `ballast_lid` | optional mass, `BALLAST` |
| `mic_gasket` `panel_gasket` `speaker_gasket` | TPU |
| `foot` | optional TPU shoe |
| `diffuser` | only with `LED_BAR = true` |
| `printplate` | one plate at a time, with the bed outline — set `PLATE` 1–6 |

Other switches: `LED_BAR` (off — costs 14 mm of height and 8 mm of depth, and
is the only soldering in the project), `FLUTES` (off — see the note on
`flutes()`), `BALLAST` (on).

## Exporting

```bash
mkdir -p cad/stl/revD && for m in shell faceplate base mic_clamp driver_clamp \
  ballast_pod ballast_lid mic_gasket panel_gasket speaker_gasket foot; do
  openscad -o cad/stl/revD/you2-$m.stl --export-format=binstl \
    -D "MODE=\"$m\"" cad/you2-revD.scad
done
```

## Printing

| | |
|---|---|
| Material | PETG for the eight rigid parts, TPU 95A for the four soft ones |
| Layer | 0.2 mm |
| Walls | 3 perimeters minimum — the mic ports and the grille need the material |
| Infill | 20 % gyroid |
| Supports | **None, on any plate** |

The shell prints **top face down**. That is the whole trick: the bottom opening
then faces up so the cavity has no ceiling, the raked face and the skirt become
steep self-supporting walls, and the four mic ports print crisp against the
plate. 141.6 cm² of bed contact, 31.2 cm² facing down at more than 60°, and the
largest downfacing feature is 9.0 cm² of 1.2 mm ledge that bridges.

## Changing it

Every dimension is in the parameter blocks at the top, and the ones that move
the whole object are marked `<-- MEASURE`. In rough order of leverage:

- **`disp_carrier`** (115.0) — the square carrier PCB. Sets the raked face
  length, which sets the height. Measure the *carrier*, not the glass; vendors
  quote whichever number flatters.
- **`spk_by`** (45) — the driver box height sets the 65 mm skirt. A shorter
  driver is the cheapest way to shrink the whole device.
- **`mic_circle_d`** (44.0) — the capsule pitch circle. **Settle this before you
  buy anything**: if Seeed's "44 mm spacing" means adjacent-capsule distance
  rather than pitch-circle diameter, it is 62.2 mm and the model refuses to
  render. See [`../hardware/measure-first.md`](../hardware/measure-first.md).
- **`pi_*`** — for a different board, change the footprint and hole spacing.
  Nothing else depends on it.
- `W` and `D` are hand-picked constants, not derived. The asserts *guard* them —
  raise `W` if the flange assert fires, raise `D` if the mic array does.

## Known soft spots

- **Every dimension came from a datasheet, not calipers.** Manifold means the
  mesh is sound, not that the display fits. Print the faceplate first: it is
  56 g and confirms four of the seven numbers that matter.
- The bezel overlaps the panel border by `(disp_panel_d - aperture)/2`. **That
  overlap is the only thing retaining the glass** — if the panel measures
  smaller than assumed it goes to zero and the display falls out.
- The rear I/O window is one generous opening rather than fitted cutouts,
  because Pi port positions shift between revisions. Print a snap-in bezel once
  you can measure the board.
- Nothing here has been printed.

Build document: [`../docs/build.html`](../docs/build.html) ·
State: [`../handoff_hardware.md`](../handoff_hardware.md)

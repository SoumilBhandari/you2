# CAD

> ## ⚠ This file describes rev C. Build `you2-revD.scad`.
>
> Everything below documents `you2.scad` — an 18° face angle, a "print the raked face flat on the
> bed" orientation that buries 1.6 litres of support inside a sealed cavity, parameter names that no
> longer exist (`face_angle`, `disp_glass_od`, `spk_d`), and a six-entry MODE table where rev D has
> sixteen. It is kept as the record of how the form was arrived at.
>
> **rev C cannot be built.** Its floor is sealed by a 0.9 mm membrane, it exports as two
> disconnected solids, nothing retains the display and nothing fastens the shell closed.
>
> For the current model, read its own header comment, and:
>
> ```bash
> python3 cad/check.py cad/you2-revD.scad   # every part is ONE connected solid
> python3 cad/fit.py   cad/you2-revD.scad   # no two parts occupy the same space
> ```
>
> Render PNGs with `--render`. **Never preview** — the CSG tree exceeds OpenSCAD's preview
> normaliser and silently produces a blank image.
>
> Build document: [`../docs/build.html`](../docs/build.html) ·
> State: [`../handoff_hardware.md`](../handoff_hardware.md)

---

`you2.scad` — parametric enclosure. Needs [OpenSCAD](https://openscad.org) 2021.01 or newer.

## Viewing it

Open the file, then `F5` for a fast preview or `F6` for a full render. Change `MODE` at the top of the file:

| MODE | What you get |
|---|---|
| `"exploded"` | assembly pulled apart — start here |
| `"assembly"` | everything in place |
| `"shell"` | the printable main body |
| `"base"` | the printable base plate with board standoffs |
| `"section"` | half-sectioned, for checking internal clearances |
| `"faceplate"` | just the display aperture region, for quick iteration |

Export an STL with `F6` then `File → Export → Export as STL`.

> **Renders clean, geometry validated.** Both parts export as manifold solids with `Status: NoError`. Pre-baked renders are in `renders/`, STLs in `stl/`.
>
> **Still not test-printed**, and every part dimension came from a datasheet rather than calipers. Manifold means the mesh is sound, not that the display fits.

## Changing it

Every dimension lives in the parameter blocks at the top. The ones most likely to need touching:

- `disp_carrier` / `disp_glass_od` — measure both. Carrier PCB and visible glass are different numbers and vendors are inconsistent about which one they advertise. **`disp_carrier` drives the whole device size** — it sets the raked face length, which sets the height. Currently 115 mm leaves 5.06 mm clear at each end of a 125.1 mm face, which the model echoes on every render so you can watch it.
- `bar_len` / `bar_w` — measure your sticks.
- `spk_d` — grille field diameter, deliberately smaller than the driver.
- `face_angle` — 18° suits a device sitting on a desk below eye level. Raise it if yours will sit higher.
- `skirt_h` — the vertical front section. Both the light bar and the speaker live here; shrink it and they collide.
- `pi_*` — currently Raspberry Pi 5. For a different board, change the footprint and hole spacing; nothing else depends on it.

## Printing

| | |
|---|---|
| Material | PETG. PLA will sag if the device sits in sun or runs warm. |
| Layer | 0.2 mm |
| Walls | 3 perimeters minimum — the mic ports and grille need the material |
| Infill | 20 % |
| Supports | None needed if the shell prints face-down |

Orient the shell with the **angled front face flat on the bed**. Everything then overhangs at less than 45°, the grille and display aperture come out clean, and the visible face gets the best surface finish. The base plate prints flat separately.

## Rendering to images yourself

```
openscad -o out.png --imgsize=900,880 --viewall --autocenter \
  --camera=0,0,0,66,0,30,0 --colorscheme="Tomorrow Night" \
  -D 'MODE="shell"' you2.scad
```

Export an STL and check it's sound in one go — the render log prints `manifold` and a status:

```
openscad -o you2-shell.stl --render -D 'MODE="shell"' you2.scad
```

## Known soft spots

- **The face has 5 mm of material at each end.** That is the tightest thing in the model and it comes straight from the display size. There's an internal stiffening frame behind the carrier, but this is the first place to expect flex — and the first thing to recheck once you can measure the real panel.
- Mic ports go straight through the top wall and already have a small outer chamfer so they don't whistle.
- The bar diffuser slot is 4.5 mm through a 2.4 mm wall. Print the diffuser insert separately in translucent PETG.
- The rear I/O window is one generous opening rather than fitted cutouts, because Pi port positions shift between revisions. Print a snap-in bezel once you have the board in hand and can measure.

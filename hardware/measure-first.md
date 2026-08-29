# Measure first

Every dimension in `cad/you2-revD.scad` came from a datasheet or a vendor
listing. Vendor listings for these parts are routinely wrong, and several of
them are wrong in ways that scrap a print.

**Do not print the shell until this table is filled in.** It is a ~30 h print
and 400 g of filament. The faceplate is 56 g and about 4 h, so iterate on that.

The parameters below are the ones the enclosure actually derives from. Change
them and the model re-sizes itself — that is deliberate, and it is the main
difference between rev D and rev C, where the form was fixed first and three
parts then did not fit inside it.

## The seven that change the object's size

| # | Parameter | Assumed | Measured | How to check |
|---|---|---|---|---|
| 1 | `disp_carrier` | 115.0 mm | | Calipers across the carrier PCB, both axes. Sets the raked face length, which sets ~107 mm of the 189 mm height. |
| 2 | `disp_active` / `disp_panel_d` | 87.6 / 91.5 mm | | **Two different numbers.** Active area is what you see; panel OD is what the bezel grips. Vendors advertise whichever is flattering. The bezel overlap `(disp_panel_d - aperture)/2` is the only thing retaining the glass — if `disp_panel_d` is smaller than assumed, that overlap goes to zero and the panel falls out. |
| 3 | `disp_hole_p` | 105.0 mm | | The carrier's own mounting-hole pitch. The display bolts straight to the faceplate on these. If the carrier has no mounting holes, the faceplate needs a retainer frame instead — tell me and it goes back in. |
| 4 | `mic_circle_d` | **44.0 / 62.2 mm?** | | **The single highest-risk number in the model, and it is genuinely ambiguous.** Seeed says "44 mm spacing arranged in a circular layout". If that is the pitch-circle diameter, the design works as drawn. If it means the distance between *adjacent* capsules — which is what "spacing" means on the linear-4 sibling — the pitch circle is 44 × √2 = **62.2 mm**, and at that value the model *refuses to render*. Precisely: the ports do **not** fall off the board — the counterbore's outer edge sits 2.3 mm inside a Ø73 PCB — but that breaks the model's 4 mm edge keep-out by 1.7 mm, and 2.3 mm of rim is not enough to bond a Ø6 membrane onto. A redesign, not a tolerance. rev C used 58 mm, which appears to be the array's *outline*, not its capsule pitch circle. Every port then misses every capsule and the array hears the cavity instead of the room. Put the array under a lamp, find the four capsule holes, measure centre to centre across the diagonal, divide by √2. |
| 5 | `spk_bx/by/bz` | 50 × 45 × 22 mm | | The BOM's speaker is an *enclosed* rectangular box, not the Ø44 cylinder rev C modelled. Its height drives the 65 mm skirt, which is 34 % of the object's height. A shorter driver is the cheapest way to shrink the whole device. |
| 6 | `spk_cone_d` | 36 mm | | The radiating diameter, not the box. Grille holes outside this are blocked and contribute nothing. |
| 7 | `mic_pcb_d` | 73.0 mm | | With the rake, this sets the depth. 150 mm currently leaves 13 mm spare. |

## The ones that stop it booting or working

| Item | What to verify | Why |
|---|---|---|
| **Waveshare panel — the right part number** | `3.4inch DSI LCD (C)`, and the overlay name `vc4-kms-dsi-waveshare-panel,3_4_inch` | ~~An earlier draft of this file said early revisions of this panel prevent a Pi 5 booting.~~ **That was wrong.** The Rev 2.2 / capacitor / power-detection caveat is on Waveshare's **4.3″ and 5″** DSI wiki pages, not the 3.4″ (C) — whose FAQ mentions Pi 5 four times with no revision caveat at all. What *is* real: a near-identical **older 3.4″ part** uses a different overlay name. Write the part number on the box. |
| **DSI cable** | 22-pin, not 15-pin | The box ships 15-pin. A Pi 5 needs 22-pin. Wrong cable = black screen, no error message. |
| **reSpeaker SKU** | Lite (2 mic, rectangular) vs Flex XVF3800 Circular-4 (4 mic, 73 mm round on FPC) | The BOM buys the Lite. The enclosure models the Flex. **These are not interchangeable** — four ports on a 44 mm circle over a two-mic rectangular board is four holes over nothing. Pick one and make the CAD match. |
| **reSpeaker sample rate** | Confirm the USB-audio descriptor's supported rates | Both boards are reported to cap at 16 kHz over UAC2. If so, the 4-mic upgrade buys beamforming, not bandwidth, and TTS quality is capped regardless of which voice you pick. |
| **Speaker leads** | Confirm bridged (BTL) output | If the driver output is bridged — and the arithmetic in the audit says it is — **neither lead may be grounded** and neither may be used as a reference. Grounding one destroys the amplifier. |
| **Acoustic vents** | GORE **GAW3343.06.0**, ID 3.0 / OD 6.0 | `mic_mem_d` is 6.2 to locate a 6.0 mm disc. An earlier draft named GAW331; that series does not exist. Adhesive side toward the housing. |
| **PSU** | Genuine Raspberry Pi 27 W | The Pi 5 caps total USB current at 600 mA unless it detects Raspberry Pi's own PD profile. You are hanging a bus-powered mic array off that. |

## The three you can only get with a soldering iron and a room

These cannot be simulated, and they are ranked by how much they would hurt.

1. **Barge-in.** Play a long TTS response at normal volume; say the wake word over it from 2 m and from 4 m. If this fails nothing else matters. Test it on a bench with the parts loose, *before* the enclosure exists — then again inside the box, because the box is what couples the driver to the mics.
2. **Continuous power draw.** An inline USB-C power meter, idle, screen on, before any load test. The design justifies passive cooling with "the load is bursty", and that argument was made about the CPU only — it does not cover the backlight or the UI render loop, which are on all the time.
3. **SoC temperature at the 60 mm rear clearance**, with `vcgencmd measure_temp` and `get_throttled` logged for an hour. All venting is on the rear face. Pushed flat against a wall the box is sealed.

## Write the numbers here

When you have the parts in front of you, fill the "Measured" column above,
change the parameters at the top of `cad/you2-revD.scad`, and re-run:

```bash
openscad -o /dev/null --export-format=binstl -D 'MODE="shell"' cad/you2-revD.scad
```

The model asserts its own buildability — face length against the carrier, top
face against the array, skirt against the driver box, ports against the array
PCB. If a measurement breaks one of those it fails loudly with the reason,
rather than rendering a shell that cannot be assembled.

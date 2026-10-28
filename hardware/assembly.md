# Assembly

**The full build document, with every part, print plate, fastener and step, is
[`docs/build.html`](../docs/build.html)** — open it in a browser. This file is
the short version and the service notes.

Order matters in one specific way: **the display never enters the shell.** It
bolts to the faceplate, from outside, and the faceplate goes on last. That is
the entire reason the face is a separate part — the previous revision had no way
to install the display at all.

## Parts

| | Part | Material | Plate |
|---|---|---|---|
| 1 | shell | PETG, 392 g | 1 — **top face down**, no support |
| 2 | base | PETG, 91 g | 2 |
| 3 | driver clamp | PETG, 5 g | 2 |
| 4 | faceplate | PETG, 55 g | 3 — face down |
| 5 | mic clamp ring | PETG, 9 g | 3 |
| 6 | ballast pod ×2 + lid ×2 | PETG, 54 g | 4 — optional |
| 7 | mic, panel and speaker gaskets | TPU 95A, 9 g | 5 |
| 8 | foot | TPU 95A, 47 g | 6 — optional |

676 g total. Every plate fits a 220 × 220 bed. No plate needs support.

## Fasteners

24 brass heat-set inserts, 24 screws, 4 standoffs. Every insert hole is bored
**smaller** than the insert's own diameter (Ø4.0 for a 4.6 mm M3, Ø3.6 for a
4.0 mm M3, Ø3.2 for a 3.5 mm M2.5) — bore it at the insert diameter and it
spins out the first time you torque a screw. **No thread is cut into plastic
anywhere** — PETG at a 45 °C interior relaxes a tapped thread's preload
over weeks.

| Joint | Insert | Screw | Qty |
|---|---|---|---|
| Faceplate → shell | M3 4.6 × 5.8 | M3 × 10 socket | 4 |
| Base → shell | M3 4.6 × 5.8 | M3 × 10 countersunk | 4 |
| Driver clamp → skirt | M3 4.6 × 5.8 | M3 × 10 socket | 2 |
| Mic clamp → ceiling | M3 4.0 × 4.0 | **M3 × 6** socket | 2 |
| Ballast pods → base | M3 4.0 × 4.0 | M3 × 8 socket | 4 |
| Display carrier → faceplate | M2.5 3.5 × 5.0 | M2.5 × 6 | 4 |
| Pi standoffs → base | M2.5 3.5 × 5.0 | 6 mm M–F standoff | 4 |
| Pi → standoffs | — | M2.5 × 6 | 4 |

## Order

1. **Install all 24 inserts.** Iron at ~200 °C, straight down, let each cool
   before loading. Getting one crooked means reprinting that part.

   **Orientation matters and is not the same for all of them.** A molten insert
   falls out of a hole that faces downward, so:

   | Inserts | Shell orientation |
   |---|---|
   | 2 mic clamp (in the ceiling) | shell **inverted**, top face on the bench |
   | 4 faceplate, 4 base corner, 2 driver | shell **upright** |
   | faceplate's 4 display posts, base's 8 | part flat on the bench |

   The two mic inserts sit ~184 mm down a blind well. Use a stubby iron or a
   long tip; a full-size iron will not reach past the shoulder.
2. **Acoustic membranes** — GORE GAW3343.06.0, ID 3.0 / OD 6.0 — into the four
   counterbores on the **outside** of the
   top face. Mesh behind the speaker grille from the inside.
3. **Ballast pods** (optional) — fill with dry sand, cap, glue the seam.
4. **Base** — standoffs, then the Pi, long axis front-to-back, **USB-A and
   Ethernet facing the rear**. Check the microSD lines up with the service slot.
   Screw the pods down either side.
5. **Driver** — TPU speaker gasket onto the raised seat, driver onto the
   gasket, clamp bar, two M3 × 10. Snug, not tight: the gasket is the mount and
   crushing it defeats the decoupling. The seat overlaps the driver's face by
   3 mm all round — that overlap is what seals the front chamber off from the
   cavity the microphones sit on.
6. **Mic array** — gasket, array, clamp ring, two **M3 × 6** (not ×8: an ×8
   bottoms in the blind bore 0.3 mm before the clamp lands, which is exactly the
   gasket squash the design depends on, so the seal never happens). Tighten until the
   screws bottom on the bosses; they are hard stops, so the gasket compresses
   exactly 0.3 mm. **Check each capsule sits under its port first.**
7. **reSpeaker core board** — cable-tie to a side rib, USB to the Pi.
8. **Power** — grommet in, PSU plug through from outside, into the cable clamp,
   into the Pi. The clamp grips the jacket, never the connector.
9. **Display onto the faceplate** — panel gasket, panel, carrier, four M2.5 × 6.
   The bezel overlap is the entire retention; if the panel is loose, stop and
   re-measure `disp_panel_d`.
10. **Connect the FFC to DSI-2** with the parts side by side, close the ZIF and
    glue the latch, fold the slack, then seat the faceplate — four M3 × 10.
11. **Base on** — four M3 × 10 countersunk.
12. **Foot** — push on, or four silicone bumpers.

## First power-up

Do it **outside the enclosure**, on a bench, with a monitor on micro-HDMI. Once
assembled, HDMI faces a side wall and you cannot reach it.

Then, assembled:

```bash
vcgencmd measure_temp && vcgencmd get_throttled     # expect throttled=0x0
arecord -l && aplay -l                              # one UAC2 card, both directions
```

Leave it an hour at the 60 mm rear clearance the venting requires and read the
temperature again.

## Service

| Job | What comes off |
|---|---|
| microSD | Nothing. Slot in the base, under the board's USB-C edge. |
| Driver clamp screws | Nothing — both are on a straight line through the rear I/O window. |
| Display, FFC | Four faceplate screws, from the front. |
| Driver, mic array, Pi | Four base screws, from below. |
| Everything | Eight screws. |

The display is the expensive part and the likely casualty of a knock. It is also
the one you can replace without touching anything else. That is deliberate, and
it is most of what "tough" means for an object that will eventually be knocked
off a desk.

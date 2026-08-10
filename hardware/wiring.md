# Wiring

Nothing here has been built. Every claim that can be checked against a
datasheet has been; the ones that cannot are marked.

## The topology, in one picture

```
                 ┌─────────────────────────── shell ──────────────────────────┐
                 │                                                            │
  mains ──┬── official Pi 27 W USB-C PD brick, captive cable                  │
          │        │                                                          │
          │        └── Ø14 grommet ──── open cable saddle ────────────────┐   │
          │           (rear face)        (grips the jacket, not the plug) │   │
          │                                                              ▼   │
          │                                                        Raspberry Pi 5
          │                                                          │  │   │
          │                    ┌─────────────────────────────────────┘  │   │
          │                    │ USB-A                        DSI-2 ────┘   │ 5 V hdr
          │                    ▼                                 │          │  + GND
          │            reSpeaker (UAC2)                          ▼          ▼
          │             │           │                     3.4" DSI panel ◄──┘
          │             │ capture   │ playback (BTL)        (needs its own 5 V —
          │             ▼           ▼                        the FPC cannot feed it)
          │        4× MEMS      enclosed driver
          │        on gasket    on gasket
          └────────────────────────────────────────────────────────────────────┘
```

**One mains plug.** That is a decision, not an omission — see "the second
supply" below.

## The only circuit: the LED bar (optional)

If you build without the light bar — the default in `cad/you2-revD.scad`, and
the recommendation — **there is nothing to solder in this build**. Everything
else is USB, a DSI ribbon, a JST plug and screws. If you build with it:

```
Pi GPIO18 ──► 74AHCT125  ──► 330 Ω ──► WS2812 DIN
   3.3 V      (Vcc = 5 V)   (at the
                             DRIVER end)
                   ▲
    5 V ───────────┴──── 500 mA polyfuse ──┬── WS2812 5 V
  (Pi header)                              │
                                       1000 µF
                                           │
    GND ───────────────────────────────────┴── WS2812 GND
```

### The five things that go wrong here

1. **Unused inputs must be tied.** The 74AHCT125 has four channels; you use
   one. TI's datasheet requires every unused *input* tied to Vcc or GND — a
   floating CMOS input oscillates, draws supply current, and couples into the
   channel you are using. Tie the three unused A inputs to GND.
2. **The OE pins are active-LOW and there are four of them.** Tie the OE of
   your channel to GND to enable it, and tie the other three to Vcc to
   three-state them. Leaving any OE floating is the same bug as (1).
3. **The level shifter's Vcc must be the same rail as the strip.** If the
   shifter is powered from a rail that comes up before the strip's, it drives
   the WS2812's DIN pin above its Vcc and the input's ESD clamp conducts. With
   one supply this question cannot be asked, which is the point.
4. **The 330 Ω goes at the WS2812 end**, not at the driver end. It is there to
   damp the reflection off the LED's input capacitance; at the wrong end it
   damps nothing.
5. **The 1000 µF goes at the strip**, across its own 5 V/GND pads. At the
   supply end it is decoupling the supply, not the load.

### The second supply: delete it

`docs/bom.md` item 9 buys a separate 5 V/2 A supply for the LED rail, on the
rule "never off the Pi's 5 V pin". That rule was written against a 24-LED ring
at full white (~1.3 A). Measure the actual load from the shipped palette in
`software/you2/runtime/leds.py`: `MAX = 0.30`, sixteen 5050s, worst case is
**~0.19 A**. Even a software failure that commands full white is ~0.9 A, and
the Pi's 5 V header pins can source that when a genuine 27 W (5 V/5 A) supply
is attached and the board is drawing ~2 A.

So: one supply, and put a **500 mA polyfuse** on the LED rail. Worst case the
fuse opens and the light bar goes dark, instead of the Pi browning out
mid-sentence. That is the correct failure mode, it costs $0.30, and it removes
a mains plug, a cable entry, $8, and the only ground loop in the box.

## Grounding

**There is no metal in this box.** The base plate is printed PETG, so there is
no chassis and no star point in the usual sense. The single ground node is the
**Pi's own ground plane**, and every return goes back to one point on its
header — not to whichever ground pin happens to be nearest.

- reSpeaker ground arrives over USB. Do not add a second ground path to it.
- LED ground returns to the same header block the 5 V came from — **not** to a
  convenient nearby ground. WS2812 return current is fast-edged; sharing a
  return with anything analogue is the dominant coupling mechanism here, and it
  is far larger than any radiated effect.
- **The speaker output is bridged (BTL).** Both leads swing. **Neither may be
  grounded, and neither may be used as a reference for anything.** Grounding
  one destroys the amplifier. Verify this against your exact reSpeaker SKU
  before you connect anything — it is asserted from the board's amplifier
  arithmetic, not read off a datasheet page.
- The display's 5 V feed comes off the Pi header. That creates a second ground
  path in parallel with the DSI ribbon's grounds. Keep the 5 V and its return
  as a **twisted pair routed alongside the ribbon**, so the loop area is small;
  do not run the 5 V up one side of the box and let the return go through the
  ribbon.

## Routing rules

The box is 152 mm wide and every noisy thing is close to every sensitive
thing. Three rules, in order of how much they matter:

1. **Never share a return.** Shared-return inductance dominates. Every load
   gets its own pair back to the one point on the Pi header.
2. **Cross at right angles.** Where the LED data line and the speaker leads
   must cross, cross them at 90° and keep them apart elsewhere.
3. **Keep the mic FPC away from everything.** On the Flex the array is on a
   20 cm ribbon carrying PDM. Route it up the *opposite* side of the box from
   the speaker leads and the LED line, and do not coil the slack — fold it.

## Strain relief and retention

An always-on desk object gets knocked, and every connector in it is a
surface-mount part on a board you cannot easily replace.

| Connector | Retention |
|---|---|
| Mains USB-C | The PSU's own captive cable passes through a Ø14 grommet in the rear face and into an open saddle (`cable_clamp()` in the CAD) that is closed over the jacket with a zip tie through its two side holes. A tug loads the shell, not the Pi's surface-mount jack. **No panel-mount coupler** — that would put two more contact interfaces in series with a 5 A rail feeding a board that is fussy about supply voltage. |
| DSI ribbon | ZIF latch closed, plus a dab of hot glue on the latch — a ZIF has no retention against vibration. Respect the FFC's ~5 mm minimum bend radius; fold it, never crease it. |
| USB to reSpeaker | Short cable. The core board straps to the four cable-tie slots in the base plate. |
| Speaker JST | Retained by the driver clamp bar. |

## ESD

The user's finger lands on touch glass 100 mm from an unearthed Pi, and the
whole assembly is plastic on a plastic foot. There is no earth anywhere in this
build (the PD brick is double-insulated, two-pin).

With an all-plastic enclosure there is no conductor to absorb a strike, so
there is no good mitigation available inside the box — only three partial ones,
and it is worth saying plainly that this is the weakest part of the electrical
design:

1. The display carrier's ground already returns through the DSI ribbon. Keep
   that ribbon short and folded, so the discharge path has low inductance.
2. Do not add a second ground path to the carrier. Two paths make a loop, and
   a loop is what turns a strike into a differential voltage across the link.
3. If it proves to be a real problem in use, the fix is a conductor: a
   1.5 mm aluminium or steel plate bonded to the Pi's ground and screwed to the
   base's four inserts. That is an *addition* to the printed design, not a
   change to it — the plate would sit inside, and the shell does not care.

None of this has been tested.

## Before you connect anything

Read [`measure-first.md`](measure-first.md). Two items there can destroy
hardware rather than merely waste a print: the bridged speaker output, and the
Waveshare panel revision that reportedly prevents a Pi 5 booting at all.

# hardware/

The physical build: what connects to what, what order to build it in, and what
to measure before you spend money.

This directory was empty until now, and that was the single biggest gap in the
project — the enclosure was modelled, the parts were costed, and there was no
document that told you how to wire them together. You cannot execute a build
from a BOM and an STL.

| file | what it is |
|---|---|
| [`../handoff_hardware.md`](../handoff_hardware.md) | **state of the physical half — read this first** |
| [`../docs/build.html`](../docs/build.html) | the full build document: every part, plate, fastener and step |
| [`wiring.md`](wiring.md) | the circuits, the grounding topology, and the routing rules |
| [`assembly.md`](assembly.md) | build order, torque, gaskets, and the service procedure |
| [`measure-first.md`](measure-first.md) | **read this before ordering.** The dimensions the CAD assumes, and how to check each one |

## The one-paragraph version

A Raspberry Pi 5 sits on brass standoffs in the bottom of a printed PETG wedge,
with two optional sand-filled ballast pods either side of it. A
round DSI display bolts to a removable faceplate on the raked front. A USB mic
array sits under the top panel on a TPU gasket that both seals its four
capsules to their ports and decouples it from the panel. An enclosed driver is
clamped against a gasket land in the skirt and fires forward. One USB-C cable
goes in at the back through a grommet and an open cable saddle that closes over
the jacket with a zip tie, so a tug loads the shell and not the Pi's
surface-mount jack. There is no second power
supply and, in the default build, nothing to solder.

## Standing electrical decisions

- **One power inlet.** A device with two mains plugs is a device with a ground
  loop and a support burden. See [`wiring.md`](wiring.md) for how the LED rail
  is fused instead.
- **Audio stays on USB.** Playback and capture must be the same UAC2 device, or
  the AEC has no sample-aligned reference. This is 80 % of why the reSpeaker is
  the pick, and it is not negotiable for a few milliseconds.
- **Nothing floats.** Every unused logic input is tied, every connector is
  retained, every cable is clamped. Listed as rules in `wiring.md` because
  every one of them is a failure that only shows up after assembly.
- **The speaker output is bridged.** Neither speaker lead may be grounded, and
  neither may be used as a reference for anything.

## What is still unknown

Everything acoustic and everything thermal. Both are listed with the specific
measurement to make in [`measure-first.md`](measure-first.md). Nothing in this
directory has been built — it is a plan, and it says so.

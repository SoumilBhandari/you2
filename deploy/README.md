# Putting a brain on an SD card

Two ways in. Both end with a device that boots, shows its eye, listens for
**"Friday"**, and consolidates at 03:00 without anyone logging in.

```
flash.sh      write Raspberry Pi OS to a card and drop the brain onto it
              — no keyboard, no monitor, no ssh session ever needed
install.sh    run on a Pi that already boots, to get to the same place
```

---

## The short version

```bash
# On your machine, with the card in a reader:
./deploy/flash.sh --image ~/Downloads/raspios-lite-arm64.img.xz --device /dev/disk4 \
                  --wifi "SSID:password" --hostname you2

# Put the card in, plug in the 27 W supply, wait ~4 minutes.
# The eye comes up on the round display. Say "Friday".
```

`flash.sh` writes the image, then mounts the boot partition and leaves behind
`firstrun.sh`, the config fragment, and the whole `software/` tree. Nothing
needs a network at flash time except the OS image you already downloaded.

---

## What ends up on the card

| | |
|---|---|
| `/opt/you2` | the brain, and a venv |
| `/var/lib/you2/brain.db` | **the memory.** One SQLite file. This is the product. |
| `/etc/you2/you2.toml` | tunables, and the API key if you use one |
| `you2.service` | the brain, the eye at `127.0.0.1:8765`, the light bar |
| `you2-kiosk.service` | Chromium, fullscreen, pointed at the eye |
| `you2-backup.timer` | nightly copy of the memory to `/var/lib/you2/backups` |

**Back up `brain.db` and nothing else.** Everything else on the card is a
download; that file is the only thing in the system that cannot be replaced,
and "copy the file" is deliberately the entire backup story.

---

## Four things that will cost you an afternoon each

These are not hypothetical — each is a documented failure with a silent
symptom, and `install.sh` handles all four.

**The display box ships 15-pin cables and a Pi 5 needs 22-pin.** Wrong cable
gives a dead black screen with no error and nothing in `dmesg`. You need
Adafruit #5822 or equivalent.

**The panel must be fed 5 V separately**, from the header or its own Type-C.
The DSI ribbon cannot supply enough current, and skipping it gives a display
that half-works — which reads as a software bug for about a day.

**`rpi_ws281x` is dead on Pi 5.** It drove NeoPixels through PWM/DMA blocks
that moved behind RP1 and it fails with `ws2811_init failed with code -3`.
Every tutorial ranking above this one is wrong. The 2026 method is the
in-tree PIO overlay, which needs no root and gives you `/dev/leds0`.

**Buy the official 27 W supply.** The Pi 5 silently caps *total* USB current
at 600 mA unless it detects Raspberry Pi's own PD profile, which most
third-party "27 W" bricks do not implement. A bus-powered mic array exceeds
that, and the failure mode is your audio device vanishing mid-session — which
you will spend days blaming on ALSA.

---

## No fan

`config.txt` sets `fan_temp0=70000` so a fitted fan never spins under normal
load. This is acoustic, not thermal: the official Active Cooler is 35–40 dBA
at half a metre, your microphones sit 50–80 mm away on the same rigid
chassis, and that lands around 55 dBA at the capsules against speech at ~60.
Five decibels of SNR where far-field ASR wants twenty. A fan does not degrade
your wake word, it deletes it — and the XMOS chip cancels far-end echo from
the speaker, not near-field mechanical noise from 10 cm away.

The load is bursty, not sustained: a voice turn is 2–5 s, and passive Pi 5
cooling only throttles after 200–300 s of continuous full load. Removing
local inference is what makes silent operation possible.

---

## Privacy, as configured

`you2.service` binds the eye to `127.0.0.1`. The memory store is on that
server, and a device whose entire pitch is that nothing leaves the room has no
business listening on `0.0.0.0`. `--host` exists for developing against it
from a laptop and prints a warning when you use it.

Raw audio is processed and discarded — never written, never sent. What
persists from a turn is text and seven prosodic floats, and those cannot be
reconstructed into audio.

If you set `llm_base_url` to a cloud provider, prompts carrying retrieved
memories leave the house on every turn. That is the honest boundary, it is one
line of config, and `you2 stats` will tell you which side of it you are on.

---

## Verifying it came up

```bash
systemctl status you2 you2-kiosk
journalctl -u you2 -f
curl -s localhost:8765/api/state
you2 stats --db /var/lib/you2/brain.db
```

`ls /dev/leds0` should exist. `wlr-randr` should show the panel as **`DSI-2`**
— not `DSI-1`, which breaks every copy-pasted command you will find.

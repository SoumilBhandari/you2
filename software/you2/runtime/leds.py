"""The light bar.

The bar is not decoration. A voice device has to tell you what state it is in,
and light is the only channel that does it without interrupting you — it
survives being read from the far side of the room with your back turned, which
the screen does not.

It deliberately duplicates the screen's state. The screen carries content; the
bar carries *is it awake*.

**Pi 5 path only.** `rpi_ws281x` drove NeoPixels through PWM/DMA blocks that
moved behind RP1 and it fails with `ws2811_init failed with code -3`. The 2026
method is the in-tree PIO overlay:

    dtoverlay=ws2812-pio,gpio=18,num_leds=16

which needs no root and exposes `/dev/leds0`. Off a Pi, or with no device
node, every method here is a no-op — so the same code runs on a laptop.

Brightness is clamped hard. WS2812 draws ~18 mA **per channel**, not per LED,
so sixteen pixels at full white is ~0.9 A; at 0.3 it is ~0.27 A, which is what
the separate 5 V rail is sized for.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .events import BUS, Bus, State

DEVICE = Path("/dev/leds0")
COUNT = 16

#: Ceiling on any channel. See the current note above -- this is a power
#: budget, not an aesthetic preference.
MAX = 0.30

#: (r, g, b) at full scale, plus the animation each state uses.
PALETTE: dict[State, tuple[tuple[int, int, int], str]] = {
    State.BOOT:        ((60, 60, 90), "sweep"),
    State.IDLE:        ((30, 40, 90), "breathe"),
    State.LISTENING:   ((40, 220, 160), "level"),
    State.REMEMBERING: ((140, 110, 255), "sweep"),
    State.THINKING:    ((255, 180, 70), "sweep"),
    State.SPEAKING:    ((255, 130, 60), "level"),
    State.DREAMING:    ((140, 90, 220), "breathe"),
    State.ERROR:       ((255, 60, 90), "blink"),
}


@dataclass
class Bar:
    count: int = COUNT
    device: Path = DEVICE
    fps: float = 30.0

    def __post_init__(self) -> None:
        self._level = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fh = None
        try:
            if self.device.exists():
                self._fh = self.device.open("wb", buffering=0)
        except OSError:
            self._fh = None      # present but not writable; carry on dark

    @property
    def live(self) -> bool:
        return self._fh is not None

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))

    # ------------------------------------------------------------- rendering

    def frame(self, state: State, t: float) -> list[tuple[int, int, int]]:
        base, mode = PALETTE.get(state, PALETTE[State.IDLE])
        out: list[tuple[int, int, int]] = []
        for i in range(self.count):
            if mode == "breathe":
                k = 0.35 + 0.35 * math.sin(t * 1.1 + i * 0.06)
            elif mode == "sweep":
                # A travelling packet: the same idea as the screen, so the two
                # read as one device rather than two animations.
                pos = (t * 2.2) % (self.count + 6) - 3
                k = max(0.06, 1.0 - abs(i - pos) / 2.6)
            elif mode == "level":
                centre = (self.count - 1) / 2
                reach = self._level * (self.count / 2 + 1)
                k = 0.12 + 0.88 * max(0.0, 1.0 - abs(i - centre) / max(0.8, reach))
            elif mode == "blink":
                k = 0.9 if int(t * 2) % 2 == 0 else 0.1
            else:
                k = 0.5
            scale = k * MAX
            out.append((int(base[0] * scale), int(base[1] * scale),
                        int(base[2] * scale)))
        return out

    def write(self, pixels: list[tuple[int, int, int]]) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(bytes(c for px in pixels for c in px))
        except OSError:
            self._fh = None      # the device went away; stop trying

    # --------------------------------------------------------------- driving

    def start(self, bus: Bus = BUS) -> None:
        if self._thread is not None:
            return

        # The bar polled bus.state and nothing else, so set_level was never
        # called and self._level stayed 0.0 for the life of the process. Both
        # LISTENING and SPEAKING render mode "level" — the two states anyone
        # actually watches were drawing a meter pinned at zero, while the audio
        # loop published a "level" event 33 times a second that had no consumer.
        q = bus.subscribe()

        def loop() -> None:
            t0 = time.monotonic()
            while not self._stop.is_set():
                while True:
                    try:
                        event = q.get_nowait()
                    except queue.Empty:
                        break
                    if event.type == "level":
                        self.set_level(float(event.data.get("level", 0.0)))
                self.write(self.frame(bus.state, time.monotonic() - t0))
                self._stop.wait(1.0 / self.fps)
            bus.unsubscribe(q)

        self._thread = threading.Thread(target=loop, daemon=True, name="you2-leds")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.write([(0, 0, 0)] * self.count)
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def __repr__(self) -> str:
        return f"Bar({self.count} px, {'live' if self.live else 'no device'})"

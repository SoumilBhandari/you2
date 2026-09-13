#!/usr/bin/env bash
# Turn a Pi that boots into a You2.
#
# Idempotent: run it again after changing anything and it converges. That
# matters more than it sounds, because the interesting failures here
# (wrong DSI cable, missing PIO node, USB current cap) are all diagnosed by
# changing one thing and re-running.
set -euo pipefail

PREFIX="${PREFIX:-/opt/you2}"
DATA="${DATA:-/var/lib/you2}"
CONF="${CONF:-/etc/you2}"
USER_NAME="${USER_NAME:-${SUDO_USER:-$(id -un)}}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/software"
BOOT_CONFIG="/boot/firmware/config.txt"     # NOT /boot/config.txt — that path
                                            # exists and is silently ignored

say()  { printf '\033[1;36m==\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -d "$SRC" ]]   || die "cannot find software/ next to this script"

# ---------------------------------------------------------------- packages

say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  python3 python3-venv python3-pip git sqlite3 \
  alsa-utils libasound2 \
  chromium-browser cage fonts-dejavu-core \
  || die "apt failed"

# ---------------------------------------------------------------- the brain

say "installing to $PREFIX"
install -d -o "$USER_NAME" -g "$USER_NAME" "$PREFIX" "$DATA" "$DATA/backups" "$CONF"
rsync -a --delete --exclude .venv --exclude __pycache__ --exclude data \
      "$SRC/" "$PREFIX/"
chown -R "$USER_NAME:$USER_NAME" "$PREFIX"

sudo -u "$USER_NAME" python3 -m venv "$PREFIX/.venv" 2>/dev/null || true
sudo -u "$USER_NAME" "$PREFIX/.venv/bin/pip" install -q --upgrade pip
sudo -u "$USER_NAME" "$PREFIX/.venv/bin/pip" install -q -e "$PREFIX"

if [[ ! -f "$CONF/you2.toml" ]]; then
  cat > "$CONF/you2.toml" <<TOML
# You2. Every tunable lives in you2/config.py; this overrides what you change.
[brain]
db_path = "$DATA/brain.db"

# The model is one base_url string: local llama.cpp, a box on the LAN, and a
# cloud API are the same code path. Leave the key out to run offline — the
# intent router and memory answers keep working.
llm_base_url = "https://api.groq.com/openai/v1"
llm_model    = "llama-3.1-8b-instant"

# The workspace capacity, in tokens. Not merely a latency budget: it is what
# forces retrieval to be a competition instead of a dump.
workspace_tokens = 400
TOML
  chown "$USER_NAME:$USER_NAME" "$CONF/you2.toml"
  chmod 600 "$CONF/you2.toml"     # it may end up holding an API key
fi

# ------------------------------------------------------------ boot config

say "boot config"
touch "$BOOT_CONFIG"
add_line() {
  grep -qxF "$1" "$BOOT_CONFIG" || echo "$1" >> "$BOOT_CONFIG"
}
add_line ""
add_line "# --- you2 ---"
# DSI costs zero GPIO: the Pi 5 has two spare 4-lane MIPI transceivers, which
# leaves the entire 40-pin header free. Every off-the-shelf round DPI panel
# consumes GPIO 0-27 with nothing spare — not even a pin for the LED data line.
add_line "dtoverlay=vc4-kms-dsi-waveshare-panel,3_4_inch"
# In-tree PIO. The old rpi_ws281x DMA path is dead on Pi 5 and fails with
# ws2811_init code -3; this needs no root and exposes /dev/leds0.
add_line "dtoverlay=ws2812-pio,gpio=18,num_leds=16"
# Acoustic, not thermal: a fan 60 mm from the mic array deletes the wake word.
add_line "fan_temp0=70000"
add_line "dtparam=audio=off"

cat > /etc/udev/rules.d/99-you2.rules <<'UDEV'
SUBSYSTEM=="*-pio", GROUP="gpio", MODE="0660"
KERNEL=="leds0", GROUP="gpio", MODE="0660"
UDEV
udevadm control --reload-rules 2>/dev/null || true

# ------------------------------------------------------------------ units

say "services"
cat > /etc/systemd/system/you2.service <<UNIT
[Unit]
Description=you2 — the brain
After=network.target sound.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$PREFIX
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$CONF/env
# Loopback only. This server carries the memory store, and the whole premise
# of the device is that nothing leaves the room.
ExecStart=$PREFIX/.venv/bin/you2 serve --config $CONF/you2.toml \\
          --db $DATA/brain.db --host 127.0.0.1 --port 8765
Restart=always
RestartSec=3
# The memory file and its backups are the only paths this needs to write.
ProtectSystem=strict
ReadWritePaths=$DATA
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/you2-kiosk.service <<UNIT
[Unit]
Description=you2 — the eye
After=you2.service
Requires=you2.service

[Service]
Type=simple
User=$USER_NAME
Environment=XDG_RUNTIME_DIR=/run/user/%U
# cage is a one-window Wayland compositor: no desktop, no panel, no cursor,
# and nothing a stray touch can escape to.
ExecStart=/usr/bin/cage -- /usr/bin/chromium-browser \\
  --kiosk --noerrdialogs --disable-infobars --incognito \\
  --hide-scrollbars --autoplay-policy=no-user-gesture-required \\
  --window-size=800,800 --window-position=0,0 \\
  --disable-features=Translate,TranslateUI \\
  http://127.0.0.1:8765/
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/you2-backup.service <<UNIT
[Unit]
Description=you2 — copy the memory

[Service]
Type=oneshot
User=$USER_NAME
# .backup rather than cp: the brain is running, WAL is live, and a plain copy
# of a live SQLite file is a corrupt copy.
ExecStart=/usr/bin/sqlite3 $DATA/brain.db ".backup '$DATA/backups/brain-%%i.db'"
UNIT

cat > /etc/systemd/system/you2-backup.timer <<'UNIT'
[Unit]
Description=you2 — nightly memory backup

[Timer]
OnCalendar=*-*-* 05:30:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now you2.service
systemctl enable --now you2-backup.timer
if [[ -e /dev/dri/card0 ]]; then
  systemctl enable --now you2-kiosk.service
else
  warn "no DRM device — skipping the kiosk. Headless is fine; the eye is at :8765"
fi

# ------------------------------------------------------------------ checks

say "checks"
ok=0
check() { if eval "$2" >/dev/null 2>&1; then printf '   ok   %s\n' "$1"; else printf '   --   %s\n' "$1"; ok=1; fi; }

check "brain responds"          "curl -sf --max-time 5 localhost:8765/api/state"
check "light bar (/dev/leds0)"  "test -e /dev/leds0"
check "display is DSI-2"        "wlr-randr 2>/dev/null | grep -q DSI-2"
check "audio capture present"   "arecord -l | grep -q card"

if ! grep -q "5A" /sys/firmware/devicetree/base/chosen/power/max_current_ma 2>/dev/null; then
  warn "could not confirm a 5 A PD supply — with an under-spec brick the Pi 5"
  warn "caps TOTAL USB current at 600 mA and your mic array vanishes mid-session"
fi

say "done. reboot for the overlays to take effect."
[[ $ok -eq 0 ]] || warn "some checks did not pass — see deploy/README.md"

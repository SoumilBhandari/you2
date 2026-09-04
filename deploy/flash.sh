#!/usr/bin/env bash
# Write a card that boots into a You2 with no keyboard, monitor or ssh session.
#
# The card is the product's first impression. Everything below exists so that
# the sequence is: flash, insert, plug in, say "Friday" — with no step where
# someone has to know what a `config.txt` is.
set -euo pipefail

IMAGE=""; DEVICE=""; HOSTNAME_="you2"; WIFI=""; COUNTRY="GB"
USER_NAME="you2"; PASSWORD=""; APIKEY="${YOU2_API_KEY:-${GROQ_API_KEY:-}}"
ASSUME_YES=0

usage() {
  cat <<'USAGE'
usage: flash.sh --image <raspios.img.xz> --device <disk> [options]

  --image       Raspberry Pi OS Lite arm64, .img or .img.xz
  --device      the card, e.g. /dev/disk4 (macOS) or /dev/sdb (Linux)
  --hostname    default: you2
  --wifi        "SSID:password"   (omit for Ethernet)
  --country     Wi-Fi regulatory domain, default GB
  --user        default: you2
  --password    login password; a random one is generated if omitted
  --api-key     LLM key; taken from $YOU2_API_KEY / $GROQ_API_KEY if set
  --yes         do not ask before erasing the card

Everything is written to the boot partition. First boot installs the rest and
takes about four minutes, most of it apt.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2;;
    --device) DEVICE="$2"; shift 2;;
    --hostname) HOSTNAME_="$2"; shift 2;;
    --wifi) WIFI="$2"; shift 2;;
    --country) COUNTRY="$2"; shift 2;;
    --user) USER_NAME="$2"; shift 2;;
    --password) PASSWORD="$2"; shift 2;;
    --api-key) APIKEY="$2"; shift 2;;
    --yes|-y) ASSUME_YES=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown option: $1" >&2; usage; exit 1;;
  esac
done

say()  { printf '\033[1;36m==\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

[[ -n "$IMAGE"  ]] || { usage; die "--image is required"; }
[[ -n "$DEVICE" ]] || { usage; die "--device is required"; }
[[ -f "$IMAGE"  ]] || die "no such image: $IMAGE"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -d "$REPO/software" ]] || die "cannot find software/ — run this from the repo"

command -v openssl >/dev/null || die "openssl is required (password hashing)"
[[ -n "$PASSWORD" ]] || PASSWORD="$(openssl rand -base64 12)"

# ---------------------------------------------------------------- confirm

say "about to ERASE $DEVICE and write $(basename "$IMAGE")"
if command -v diskutil >/dev/null; then diskutil list "$DEVICE" || true
elif command -v lsblk  >/dev/null; then lsblk "$DEVICE" || true; fi

if [[ $ASSUME_YES -ne 1 ]]; then
  # Deliberately not a y/n. Erasing the wrong disk is unrecoverable, and the
  # cost of typing seven characters is nothing against that.
  read -r -p "type ERASE to continue: " reply
  [[ "$reply" == "ERASE" ]] || die "aborted"
fi

# ------------------------------------------------------------------ write

say "writing (this takes a few minutes)"
if command -v diskutil >/dev/null; then
  diskutil unmountDisk "$DEVICE" || true
  RAW="${DEVICE/disk/rdisk}"          # /dev/rdiskN is an order of magnitude faster
else
  RAW="$DEVICE"
  sudo umount "${DEVICE}"* 2>/dev/null || true
fi

if [[ "$IMAGE" == *.xz ]]; then
  xz -dc "$IMAGE" | sudo dd of="$RAW" bs=8m status=progress 2>/dev/null \
    || xz -dc "$IMAGE" | sudo dd of="$RAW" bs=8M
else
  sudo dd if="$IMAGE" of="$RAW" bs=8m status=progress 2>/dev/null \
    || sudo dd if="$IMAGE" of="$RAW" bs=8M
fi
sync

# ------------------------------------------------------------------- mount

say "waiting for the boot partition"
BOOT=""
for _ in $(seq 1 30); do
  sleep 1
  if command -v diskutil >/dev/null; then
    diskutil mountDisk "$DEVICE" >/dev/null 2>&1 || true
    for candidate in /Volumes/bootfs /Volumes/boot; do
      [[ -d "$candidate" ]] && BOOT="$candidate" && break
    done
  else
    for candidate in /media/*/bootfs /media/*/boot /mnt/boot; do
      [[ -d "$candidate" ]] && BOOT="$candidate" && break
    done
  fi
  [[ -n "$BOOT" ]] && break
done
[[ -n "$BOOT" ]] || die "could not find the boot partition — mount it and re-run with --device already written"
say "boot partition at $BOOT"

# ------------------------------------------------------------- provisioning

say "provisioning"
touch "$BOOT/ssh"                                   # a way back in if the eye never lights
HASH="$(openssl passwd -6 "$PASSWORD")"
echo "${USER_NAME}:${HASH}" > "$BOOT/userconf.txt"

# The overlays have to be in place before first boot, or the panel and the
# light bar are absent on the very first run and it looks broken.
cat >> "$BOOT/config.txt" <<'CONFIG'

# --- you2 ---
# DSI costs zero GPIO; every round DPI panel eats GPIO 0-27 with nothing spare.
dtoverlay=vc4-kms-dsi-waveshare-panel,3_4_inch
# In-tree PIO. rpi_ws281x is dead on Pi 5 (ws2811_init code -3).
dtoverlay=ws2812-pio,gpio=18,num_leds=16
# Acoustic, not thermal: a fan 60 mm from the mic array deletes the wake word.
fan_temp0=70000
dtparam=audio=off
CONFIG

if [[ -n "$WIFI" ]]; then
  SSID="${WIFI%%:*}"; PSK="${WIFI#*:}"
  cat > "$BOOT/wpa_supplicant.conf" <<WPA
country=$COUNTRY
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
network={
    ssid="$SSID"
    psk="$PSK"
}
WPA
fi

say "copying the brain"
rm -rf "$BOOT/you2"
mkdir -p "$BOOT/you2"
# tar rather than cp: the boot partition is FAT32, and this keeps the copy
# fast and the exclusions honest.
tar -C "$REPO" \
    --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='data' --exclude='.pytest_cache' \
    --exclude='*.egg-info' \
    -cf - software deploy | tar -C "$BOOT/you2" -xf -

[[ -n "$APIKEY" ]] && printf 'YOU2_API_KEY=%s\n' "$APIKEY" > "$BOOT/you2/env"

cat > "$BOOT/firstrun.sh" <<FIRSTRUN
#!/bin/bash
# Raspberry Pi OS runs this once, as root, on first boot, then removes the
# cmdline hook. Everything it does is idempotent anyway.
set -x
exec > /var/log/you2-firstrun.log 2>&1

hostnamectl set-hostname "$HOSTNAME_" || true
sed -i "s/raspberrypi/$HOSTNAME_/g" /etc/hosts || true

install -d /opt/you2-src
cp -a /boot/firmware/you2/. /opt/you2-src/ 2>/dev/null || cp -a /boot/you2/. /opt/you2-src/
install -d /etc/you2
[ -f /opt/you2-src/env ] && install -m 600 /opt/you2-src/env /etc/you2/env

chmod +x /opt/you2-src/deploy/install.sh
USER_NAME="$USER_NAME" /opt/you2-src/deploy/install.sh || true

# Clear the first-boot hook whichever mechanism this image uses.
sed -i 's| systemd.run.*||' /boot/firmware/cmdline.txt 2>/dev/null || \
sed -i 's| systemd.run.*||' /boot/cmdline.txt 2>/dev/null || true
rm -f /boot/firmware/firstrun.sh /boot/firstrun.sh
systemctl reboot
FIRSTRUN
chmod +x "$BOOT/firstrun.sh"

CMDLINE="$BOOT/cmdline.txt"
if [[ -f "$CMDLINE" ]] && ! grep -q firstrun.sh "$CMDLINE"; then
  # One line, no trailing newline: the bootloader is unforgiving about both.
  printf '%s systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target' \
    "$(tr -d '\n' < "$CMDLINE")" > "$CMDLINE.new"
  mv "$CMDLINE.new" "$CMDLINE"
fi

sync
if command -v diskutil >/dev/null; then diskutil eject "$DEVICE" >/dev/null || true
else sudo umount "$BOOT" || true; fi

cat <<DONE

  card ready.

    hostname   $HOSTNAME_.local
    user       $USER_NAME
    password   $PASSWORD        <- write this down; it is not stored anywhere
    llm key    $([[ -n "$APIKEY" ]] && echo "installed" || echo "none — runs offline")

  Put the card in, plug in the official 27 W supply, wait about four minutes.
  The eye comes up on the round display. Say "Friday".

  If it does not:  ssh $USER_NAME@$HOSTNAME_.local  and read
  /var/log/you2-firstrun.log — and check deploy/README.md, where the four
  failures with silent symptoms are written down.

DONE

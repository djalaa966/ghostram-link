#!/usr/bin/env bash
# GhostRAM Link — PC side (Linux): attach the phone's RAM as an ENCRYPTED, low-priority swap tier.
#
#   sudo ./ghostram-up.sh                 # via `adb forward` (USB debugging on, Termux server running)
#   sudo ./ghostram-up.sh --host 192.168.42.129   # via USB tethering instead of adb
#
# Tier order after this script (highest priority first):
#   zram (if you have it, priority 100) → GhostRAM phone tier (priority 50) → your disk swap (default, negative)
#
# Safety: pages swapped to the phone exist ONLY on the phone. Pulling the cable while pages are out there
# crashes the processes that own them. Use ghostram-down.sh (or ghostram-watch.sh) to drain first.
# Read docs/SAFETY.md before using this on a machine you care about.
set -euo pipefail

HOST=127.0.0.1
PORT=10809
NBD=/dev/nbd0
PRIO=50
NAME=ghostram
USE_ADB=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; USE_ADB=0; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --nbd) NBD="$2"; shift 2 ;;
    --priority) PRIO="$2"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
for bin in nbd-client cryptsetup mkswap swapon; do
  command -v "$bin" >/dev/null || { echo "missing: $bin (Debian/Ubuntu: apt install nbd-client cryptsetup)" >&2; exit 1; }
done

modprobe nbd max_part=0 2>/dev/null || true
[[ -b $NBD ]] || { echo "$NBD not found — is the nbd kernel module available?" >&2; exit 1; }

if [[ $USE_ADB == 1 ]]; then
  command -v adb >/dev/null || { echo "missing: adb (apt install adb)" >&2; exit 1; }
  echo "→ adb forward tcp:$PORT → phone tcp:$PORT"
  adb forward "tcp:$PORT" "tcp:$PORT"
fi

echo "→ connecting $NBD to $HOST:$PORT"
# -swap: the kernel must never block on memory allocation for this socket (swap over network).
# -persist is deliberately NOT used: a silent reconnect to a different/empty export would corrupt swap.
nbd-client "$HOST" "$PORT" "$NBD" -N ghostram -s -b 4096 -t 30

# Encrypt with a random, throw-away key that never leaves this PC's kernel (plain dm-crypt, no header).
echo "→ dm-crypt (aes-xts-plain64, random key)"
cryptsetup open --type plain --cipher aes-xts-plain64 --key-size 512 \
  --key-file /dev/urandom "$NBD" "$NAME"

mkswap -L "$NAME" "/dev/mapper/$NAME" >/dev/null
# --discard lets freed swap slots be TRIMmed → the phone gives that RAM back to Android.
swapon --priority "$PRIO" --discard "/dev/mapper/$NAME"

echo "✓ GhostRAM Link active:"
swapon --show
echo
echo "Detach safely with: sudo $(dirname "$0")/ghostram-down.sh"

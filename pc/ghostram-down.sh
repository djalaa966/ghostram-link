#!/usr/bin/env bash
# GhostRAM Link — detach the phone tier SAFELY: swapoff first (the kernel brings every page back into RAM,
# zram or disk swap), then close dm-crypt, then disconnect NBD. Only unplug the cable after this finishes.
#
#   sudo ./ghostram-down.sh
set -euo pipefail
NBD=${NBD:-/dev/nbd0}
NAME=ghostram
PORT=${PORT:-10809}

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }

DEV=""
[[ -e /dev/mapper/$NAME ]] && DEV=$(readlink -f "/dev/mapper/$NAME")   # /proc/swaps lists /dev/dm-N
if [[ -n $DEV ]] && awk -v d="$DEV" '$1 == d {f=1} END {exit !f}' /proc/swaps; then
  used_kb=$(awk -v d="$DEV" '$1 == d {print $4}' /proc/swaps)
  echo "→ draining ${used_kb:-0} KB from the phone back to this PC (swapoff)…"
  # swapoff needs room: free RAM + other swap must hold what is on the phone.
  avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
  other_free_kb=$(awk -v d="$DEV" 'NR>1 && $1 != d {free += $3 - $4} END {print free+0}' /proc/swaps)
  if [[ -n "${used_kb:-}" && $used_kb -gt $((avail_kb + other_free_kb)) ]]; then
    echo "✗ not enough RAM + swap to take the pages back (${used_kb} KB needed, $((avail_kb + other_free_kb)) KB free)." >&2
    echo "  Close some applications, then run this again. Do NOT unplug the phone." >&2
    exit 1
  fi
  swapoff "/dev/mapper/$NAME"
fi

[[ -e /dev/mapper/$NAME ]] && cryptsetup close "$NAME"
nbd-client -d "$NBD" >/dev/null 2>&1 || true
command -v adb >/dev/null && adb forward --remove "tcp:$PORT" 2>/dev/null || true
echo "✓ GhostRAM Link detached — you can unplug the phone."

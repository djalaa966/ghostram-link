#!/usr/bin/env bash
# GhostRAM Link — the phone's state decides (the "reclaim" idea of the project).
#
# Polls the phone over adb every few seconds. When the phone needs its memory back — low battery,
# too hot, low free RAM, or not charging below a threshold — it drains the phone tier gracefully
# (ghostram-down.sh: swapoff → pages come home) BEFORE anything is lost.
#
#   sudo ./ghostram-watch.sh                      # defaults below
#   sudo MIN_BATTERY=30 MAX_TEMP_C=42 ./ghostram-watch.sh
#
# Limits: this protects against the phone's own needs, not against a cable being yanked.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
INTERVAL=${INTERVAL:-5}
MIN_BATTERY=${MIN_BATTERY:-25}        # % — drain below this when not charging
MAX_TEMP_C=${MAX_TEMP_C:-45}          # battery temperature, °C
MIN_PHONE_FREE_MB=${MIN_PHONE_FREE_MB:-600}

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
command -v adb >/dev/null || { echo "missing: adb" >&2; exit 1; }

drain() {
  echo "$(date +%T) ⚠ $1 → draining the phone tier"
  if "$HERE/ghostram-down.sh"; then
    echo "$(date +%T) ✓ drained. Re-attach later with ghostram-up.sh"; exit 0
  fi
  echo "$(date +%T) ✗ drain failed (not enough room on the PC) — retrying in ${INTERVAL}s"
}

echo "Watching the phone every ${INTERVAL}s (battery < ${MIN_BATTERY}% unplugged, temp > ${MAX_TEMP_C}°C, free < ${MIN_PHONE_FREE_MB} MB)"
while true; do
  if ! adb get-state >/dev/null 2>&1; then
    echo "$(date +%T) ✗ phone not reachable over adb — if the cable is out, pages on the phone are already lost."
    sleep "$INTERVAL"; continue
  fi
  bat=$(adb shell dumpsys battery 2>/dev/null | tr -d '\r')
  level=$(awk -F': ' '/^ *level:/ {print $2; exit}' <<<"$bat")
  temp10=$(awk -F': ' '/^ *temperature:/ {print $2; exit}' <<<"$bat")   # tenths of °C
  powered=$(grep -Eq '(AC|USB|Wireless) powered: true' <<<"$bat" && echo 1 || echo 0)
  free_mb=$(adb shell cat /proc/meminfo 2>/dev/null | awk '/MemAvailable/ {print int($2/1024)}')
  temp=$(( ${temp10:-0} / 10 ))
  echo "$(date +%T) battery ${level:-?}% (charging=$powered) ${temp}°C, phone free ${free_mb:-?} MB"

  if [[ -n "${level:-}" && $powered == 0 && $level -lt $MIN_BATTERY ]]; then drain "battery ${level}%"; fi
  if [[ $temp -gt $MAX_TEMP_C ]]; then drain "phone at ${temp}°C"; fi
  if [[ -n "${free_mb:-}" && $free_mb -lt $MIN_PHONE_FREE_MB ]]; then drain "phone free RAM ${free_mb} MB"; fi
  sleep "$INTERVAL"
done

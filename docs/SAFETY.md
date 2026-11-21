# Safety — read this before you try

GhostRAM Link is an **experiment**. It works with standard Linux tools, but swap is where the kernel keeps
pieces of running programs. Treat it accordingly.

## What can go wrong

| Situation | What happens | How the project mitigates it |
|---|---|---|
| Cable pulled / phone reboots / Termux killed **while pages are on the phone** | The processes that own those pages crash (SIGBUS) or hang; if kernel-critical memory was there, the system can freeze | Run `pc/ghostram-down.sh` before unplugging. Keep the phone charging, screen-off allowed, Termux with a wake lock (`termux-wake-lock`). Run `pc/ghostram-watch.sh` |
| Phone gets low on battery, hot, or short of RAM | Android may kill Termux → same as a cable pull | `ghostram-watch.sh` drains the tier (swapoff) before thresholds are hit |
| `swapoff` cannot bring the pages home | Not enough free RAM + other swap on the PC | `ghostram-down.sh` checks first and refuses; close apps, retry. Never unplug in that state |
| Somebody else on the network talks to the NBD port | They could read ciphertext or corrupt your swap | Default bind is `127.0.0.1` (adb forward only). Use `--bind 0.0.0.0` only on the USB tethering link, never on Wi-Fi you don't control |
| Privacy | Swap contains passwords, keys, documents | Everything is encrypted on the PC (dm-crypt, random key) before it leaves; the phone stores only ciphertext |
| Stock `f_ncm` / RNDIS stalls | Some kernels stall 100+ ms under load ([report](https://github.com/aurora-silicon/linux/pull/2095)) | Prefer `adb forward`; measure with `bench/` first; `nbd-client -t 30` timeout |
| Phone flash wear | None — the export lives in RAM, never on the phone's storage | — |

## Recommended first run

1. Use a machine where a crash only costs you a reboot.
2. Start with a small export (`--size 512M`) and `bench/page_client.py` to see your real latency.
3. Keep your normal disk swap enabled (lower priority): the phone tier sits *between* zram and disk.
4. Detach with `sudo pc/ghostram-down.sh`, then unplug.

## Not supported

- Hibernation onto this swap (it disappears at shutdown).
- Windows (see ARCHITECTURE.md).
- Two PCs on one phone export (the server accepts one client at a time on purpose).

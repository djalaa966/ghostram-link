# Architecture

## The honest premise

A phone's RAM can never become *real* RAM for a PC: the CPU's memory controller must drive DRAM directly,
and no phone exposes PCIe (no USB4 / Thunderbolt on phones). What a phone **can** be is a **swap tier**:
pages the PC evicts are stored in the phone's RAM and fetched back on demand. GhostRAM Link builds that tier
with standard Linux pieces, and adds two ideas to make it useful: **prefetching** to hide the cable's
latency, and letting **the phone's state decide** when its memory must be given back.

## Data path

```
 PC (Linux)                                                        Phone (Android, Termux — no root)
 ┌──────────────────────────────────────────────┐                ┌──────────────────────────────────┐
 │ apps                                         │                │                                  │
 │   │ page fault / reclaim                     │                │                                  │
 │   ▼                                          │                │                                  │
 │ RAM ──► zram (prio 100, compressed in RAM)   │                │                                  │
 │            │ full                            │                │                                  │
 │            ▼                                 │   USB cable    │                                  │
 │ /dev/mapper/ghostram  (prio 50)              │  adb forward   │  ghostram_nbd.py                 │
 │   dm-crypt aes-xts, random key (PC only)     │  or tethering  │   anonymous mmap (lazy, TRIM →   │
 │   │                                          │ ◄────────────► │   memory given back to Android)  │
 │ /dev/nbd0  (nbd-client -swap)  ──── TCP ─────┼────────────────┼─► NBD protocol: READ/WRITE/      │
 │            │ phone gone / drained            │                │   FLUSH/TRIM                     │
 │            ▼                                 │                └──────────────────────────────────┘
 │ disk swap (lowest priority)                  │
 └──────────────────────────────────────────────┘
        ▲                                   ▲
        │ ghostprofile.py replay            │ ghostram-watch.sh (adb: battery, temperature, free RAM)
        │ process_madvise(MADV_WILLNEED)    │ → ghostram-down.sh = swapoff: every page comes home
```

### Why these pieces

| Piece | Choice | Why |
|---|---|---|
| Transport | NBD over TCP (`adb forward` or USB tethering) | Works on any phone **without root**; `nbd-client -swap` is designed for swap over network |
| Phone storage | anonymous `mmap`, MAP_PRIVATE | Allocated lazily; `TRIM` → `MADV_DONTNEED` gives RAM back to Android |
| Encryption | dm-crypt plain, aes-xts-plain64, key from `/dev/urandom` | The phone only sees ciphertext; the key dies with the mapping |
| Tier order | swap priorities: zram 100 → phone 50 → disk < 0 | Pure kernel policy, nothing custom in the hot path |
| Prefetch | `ghostprofile.py` (record / replay) | User space only: `/proc/PID/pagemap` + `process_madvise` (Linux ≥ 5.15) |
| Reclaim | `ghostram-watch.sh` | The phone's battery / heat / free RAM trigger a graceful `swapoff` |

## Expected performance

Per 4 KB page (medians; see [RESEARCH.md](RESEARCH.md) for sources):

| Tier | Latency | Measured? |
|---|---|---|
| DRAM | ~0.1 µs | general knowledge |
| zram | 2–5 µs | derived |
| NVMe Gen4 / Gen3 | 49 / 83 µs | measured (CrystalDiskMark Q1T1) |
| USB 3 SSD (Samsung T7) | 190 µs | measured |
| **phone, USB 3, raw FunctionFS** | **~220 µs** | derived — nobody has published a measurement |
| laptop eMMC | 414 µs | measured |
| **phone, USB 3, NCM/TCP (this project today)** | **~550 µs** | derived |
| **phone, USB 2, NCM/TCP** | **~1 000 µs** | derived from 0.5–1 ms tether pings |

So, without prefetching, the phone tier is **not faster than a laptop's eMMC** and much slower than NVMe.
The simulator ([../sim](../sim)) and the real-kernel test ([../tests/RESULTS.md](../tests/RESULTS.md)) show
where it can still win:

- **Laptops with soldered 4–8 GB RAM and eMMC or HDD storage**, running workloads that repeat their access
  order (LLM weights, game level loads, pointer-heavy structures that are walked the same way each time).
- With recorded profiles, the simulator puts a USB 3 phone tier **up to ~6× ahead of eMMC swap** on those
  workloads, and **30–165× ahead of HDD swap**. On random access (databases) prefetching hurts unless gated.
- On a machine with NVMe, use zram + NVMe swap. The phone adds nothing.

## Windows

There is no Windows support yet, on purpose. A pagefile on the phone needs a kernel-mode StorPort virtual
miniport with a kernel-only transport (WSK or a KMDF USB client): months of work, an EV signing certificate,
and a cable pull would blue-screen the PC unless every page is mirrored locally. See
[RESEARCH.md § Windows](RESEARCH.md#windows). The realistic Windows path is a user-mode library for
specific apps (e.g. spilling an LLM runtime's KV-cache to the phone) — contributions welcome.

# Research notes (21 November 2026)

Everything the design rests on, with sources. "Measured" = a published benchmark; "derived" = inferred from
measured neighbours; anything else is marked.

## 1. Latency of one 4 KB page

CrystalDiskMark "MB" = 10^6 bytes → latency = 4096 / (MB/s × 10^6) s. These averages include the OS stack.

| Medium | Value | Type | Source |
|---|---|---|---|
| NVMe Gen4, Samsung 980 Pro, RND4K Q1T1 | 83.7 MB/s → **49 µs** | measured | https://ssd-tester.com/samsung_980_pro_1tb.html |
| NVMe Gen3, Samsung 970 EVO Plus | 49.5 MB/s → **83 µs** | measured | https://ssd-tester.com/samsung_970_evo_plus_1tb.html |
| USB 3.2 SSD, Samsung T7, RND4K Q1T1 | 21.5 MB/s → **190 µs** | measured | https://ssd-tester.com/samsung_portable_ssd_t7_1tb.html |
| Laptop eMMC, SanDisk DF4032, 4K read | 9.9 MB/s → **414 µs** | measured | https://www.notebookcheck.net/SanDisk-DF4032-Benchmarked.703684.0.html |
| USB 3 round trip, USRP B210 | 160 µs minimum | measured | https://sumiteurecom.github.io/latency-measurements-usrp.pdf |
| Cypress FX3 USB 3 bulk | ~200 µs host→device, ~600 µs device→host | forum measurement | Infineon community (FX3 latency threads) |
| xHCI interrupt moderation | 40 µs default (Linux) | documented | linux-arm-kernel list, Dec 2017 |
| Android USB tethering ping, PC → phone | 0.5–1 ms | forum measurements | forums.tomshardware.com; github.com/mkckr0/audio-share/issues/16 |
| Linux `f_ncm` gadget under load (stock) | p50 0.74 ms, **p99 213 ms** | measured | https://github.com/aurora-silicon/linux/pull/2095 |
| NCM gadget throughput | > 2 Gbps (5G), > 4 Gbps (10G) | measured | lore.kernel.org, Sept 2020 |

**Nobody has published the latency of fetching a 4 KB page from a phone's RAM over USB.** The values the
project uses for the phone are derived:

| Path | median | p99 |
|---|---|---|
| USB 2, NCM + TCP | ~1 000 µs | ~5 000 µs |
| USB 3, NCM + TCP (adb forward / tethering — what this repo uses today) | ~550 µs | ~2 500 µs |
| USB 3, raw FunctionFS bulk, phone awake (needs root) | ~220 µs | ~1 000 µs |

`bench/` exists to replace these with real numbers. Please share yours.

## 2. Prior art — what is NOT new

Being honest about this matters more than any claim of novelty.

**Borrowing another device's memory as swap / far memory**
- Micron, *Memory as a Service* patent family (priority 2019-05-28), e.g. US11100007B2: a borrower device
  places part of its virtual memory in RAM loaned by a lender device over a link, including phones.
- Nswap (Euro-Par 2003): swap to peers' RAM in a cluster. Infiniswap (NSDI 2017), Fastswap (EuroSys 2020),
  Hydra: RDMA-based far memory with disk backup.
- Microsoft ReadyBoost (US7490197B2, 2004): a USB device as a cache tier in front of the disk, safe to
  remove because the disk always has the data. Removed from the Windows 11 UI.
- Small 2026 projects close to this one: `ram-link` (phone RAM leased over TCP to Windows, no benchmarks),
  `nbd-vram` (GPU VRAM as Linux swap over NBD), an XDA thread proposing the exact idea.

**Prefetching from a recorded per-application history**
- Windows Prefetcher / SuperFetch (US6633968B2, ~2000): per-app scenario files replayed on the next launch.
- TPP (DATE 2023): records swapped-in pages at Android app launch (zram) and prefetches them next time.
- 3PO (arXiv 2207.07688, 2022): traces an app once into a "tape" and replays it to prefetch far memory.
- Online prefetchers: Leap (ATC 2020), Canvas (NSDI 2023), Linux VMA-based swap readahead, DAMOS_WILLNEED.
- Android 17 `mmd` prefetches a cached app's swapped pages when it is unfrozen (reactive, no profile);
  `Ultra RAM` (GitHub) calls `process_madvise(MADV_WILLNEED)` when you switch back to an app.

**What this repository adds** — no invention claim, just work nobody had put together in the open:
1. a **no-root, standard-tools** path to use a phone's RAM as an encrypted Linux swap tier;
2. a **user-space record / replay** prefetcher (`/proc/PID/pagemap` + `process_madvise`) whose profiles are
   keyed by mapping, not address, and self-align when the kernel merges mappings differently;
3. **phone-driven reclaim** (battery, temperature, free RAM → graceful `swapoff`);
4. **public numbers**: a simulator with sourced inputs, a real-kernel experiment, and a benchmark anyone
   can run on their own phone and cable.

## 3. Simulator summary

`sim/ghostsim.py`, 200 000 page touches per workload, RAM 4 096 pages + zram 4 096 pages (ratio 2.5).
Slowdown vs unlimited RAM (lower is better). Full table: [`sim/results/results.md`](../sim/results/results.md).

| config | LLM weights | browser | game | database | pointer chase |
|---|---|---|---|---|---|
| zram + eMMC (cheap laptop today) | 289× | 98× | 78× | 132× | 289× |
| zram + phone USB 2 | 610× | 199× | 154× | 272× | 610× |
| zram + phone USB 3 (NCM) | 326× | 106× | 82× | 146× | 326× |
| zram + phone USB 3 (FunctionFS) | 131× | 43× | 33× | 59× | 131× |
| zram + phone USB 3 + profile | **46×** | 83× | **15×** | 565× | **46×** |
| zram + NVMe | 43× | 15× | 12× | 20× | 44× |
| zram + NVMe + profile | 3.6× | 6.4× | 2.2× | 36× | 3.6× |
| zram + HDD | 7 619× | 2 547× | 2 024× | 3 460× | 7 620× |

Readings: the phone alone does not beat eMMC unless raw FunctionFS is used; prefetching is where the value
is; random access punishes prefetching; with NVMe the phone is pointless; the profile win also assumes the
USB link keeps ~16 requests in flight — an assumption `bench/page_client.py --qd 8` can check.

Robustness (browser workload): pulling the cable without a disk copy lost 6 032 pages (= crashes); a graceful
reclaim (drain before release) lost none and took ~1.6 s.

## 4. Real-kernel experiment

See [`tests/RESULTS.md`](../tests/RESULTS.md): on Linux 6.18 with a 96 MB memory limit and a 192 MB working
set, replaying a recorded profile made a full sweep **2.6× faster** (6.38 s → 2.42 s, median of 5) with
`--ahead 4096`, and **15 % slower** when the profile did not match the access order.

## Windows

- No Microsoft document states the rule, but removable / USB volumes cannot host `pagefile.sys` in practice.
  Every driver in a paging stack receives `IRP_MN_DEVICE_USAGE_NOTIFICATION` (DeviceUsageTypePaging) and must
  keep its I/O path non-pageable
  ([Handling PnP Paging Requests](https://learn.microsoft.com/en-us/windows-hardware/drivers/storage/handling-pnp-paging-requests)).
- Primo Ramdisk allows a pagefile only on pure-RAM "SCSI" disks; WinFsp states a pagefile on user-mode
  storage is impossible (deadlock: the service process may itself be paged out).
- A phone-backed pagefile therefore needs a kernel-only StorPort virtual miniport with WSK (kernel TCP over
  the tethering adapter) or a KMDF USB client talking to a FunctionFS endpoint, EV-certificate attestation
  signing (test-signing for prototypes, Secure Boot off), and a local mirror of every page to survive a
  cable pull. Estimate: 4–8 months for an experienced kernel developer. Not recommended.
- Realistic: a user-mode library for specific apps (LLM KV-cache, key-value stores) over `adb forward`.

# GhostRAM Link

**Use your phone's RAM as an encrypted swap tier for your Linux PC, over a USB cable — no root on the phone,
and prefetching that hides the cable's latency.** An open research project by [HexaLab](https://hexalab.app).

[العربية](README.ar.md) · Article: [hexalab.app/blog/ghostram-link-phone-ram-for-your-pc](https://hexalab.app/blog/ghostram-link-phone-ram-for-your-pc/)

> **Status: experiment.** It works with standard Linux tools, the numbers below are real or clearly marked
> as derived, and it can crash your apps if you unplug the phone at the wrong moment. Read
> [docs/SAFETY.md](docs/SAFETY.md) first. **You** run it, measure, and judge — then share your numbers.

## What it is — and what it is not

- **Not** "free RAM". A phone's memory cannot become real RAM for a PC: the CPU must drive DRAM directly
  and phones have no PCIe link. Anything sold as "+8 GB virtual RAM" is swap.
- **It is** a swap tier that sits between `zram` and your disk:

```
RAM → zram (compressed, in RAM) → PHONE RAM over USB (encrypted) → disk swap
```

- **It helps** laptops with soldered 4–8 GB RAM and **slow storage (eMMC, HDD)**, on workloads that repeat
  their memory-access order (LLM weights, game level loads, the same data walked the same way).
- **It does not help** a machine with an NVMe SSD (use zram + NVMe swap), or random-access workloads.

## Key numbers (one 4 KB page)

| Where the page is | Latency | |
|---|---|---|
| RAM | ~0.1 µs | |
| zram | 2–5 µs | derived |
| NVMe SSD | 49–83 µs | measured |
| **Phone over USB 3, raw (FunctionFS)** | ~220 µs | derived |
| Laptop eMMC | 414 µs | measured |
| **Phone over USB 3, TCP (this repo today)** | ~550 µs | derived |
| **Phone over USB 2, TCP** | ~1 000 µs | derived |

Nobody has published a real measurement for the phone rows yet — [`bench/`](bench) lets you produce one.

**Simulator** ([sim/](sim), sourced inputs): with recorded profiles, a USB 3 phone tier ends up **up to ~6×
faster than eMMC swap** on repeatable workloads and **30–165× faster than HDD swap**, but **no faster than
eMMC without prefetching**, and much slower than NVMe.

**Real kernel** ([tests/RESULTS.md](tests/RESULTS.md)): Linux 6.18, 96 MB RAM limit, 192 MB working set,
SSD swap. Replaying a recorded profile made the workload **2.6× faster** (6.38 s → 2.42 s, median of 5).
A wrong profile made it **15 % slower**.

## Quick start

### 1. Phone (Android, no root)

Install [Termux](https://termux.dev) (F-Droid build), then:

```bash
pkg install python
termux-wake-lock                       # keep Android from sleeping the server
curl -LO https://raw.githubusercontent.com/djalaa966/ghostram-link/main/phone/ghostram_nbd.py
python ghostram_nbd.py --size 2G       # serves 2 GB of RAM on 127.0.0.1:10809
```

Enable **USB debugging** (Settings → About phone → tap *Build number* 7× → Developer options), plug the cable.

### 2. Measure first (any OS)

```bash
# phone (Termux):
curl -LO https://raw.githubusercontent.com/djalaa966/ghostram-link/main/bench/page_server.py
python page_server.py
# PC:
adb forward tcp:10810 tcp:10810
python bench/page_client.py --label "my phone, USB 3 cable" --json my-phone.json
python bench/page_client.py --qd 8      # how well the link overlaps requests
```

If your median is above your disk's latency, stop here — the phone tier will not help this machine.

### 3. Attach the phone tier (Linux PC)

```bash
sudo apt install nbd-client cryptsetup adb
sudo pc/ghostram-up.sh                 # adb forward + nbd + dm-crypt + swapon (priority 50)
swapon --show
sudo pc/ghostram-watch.sh              # optional: drains automatically on low battery / heat / phone RAM
sudo pc/ghostram-down.sh               # ALWAYS before unplugging: brings every page home
```

### 4. Prefetch profiles (experimental)

```bash
sudo python3 profiles/ghostprofile.py record --pid $(pgrep -n my-app) --out my-app.ghostprofile --seconds 120
# next time the app runs:
sudo python3 profiles/ghostprofile.py replay --pid $(pgrep -n my-app) --profile my-app.ghostprofile --ahead 4096
```

Needs Linux ≥ 5.15 (`process_madvise` with `MADV_WILLNEED`) and root (CAP_SYS_NICE).

## Repository

| Path | What |
|---|---|
| `phone/ghostram_nbd.py` | NBD server serving phone RAM (pure Python, Termux) — tested with `qemu-io` and `nbdinfo` |
| `pc/ghostram-up.sh` / `-down.sh` / `-watch.sh` | attach (encrypted, priority 50), safe detach, phone-driven drain |
| `bench/` | 4 KB page latency benchmark (phone server + PC client) |
| `profiles/ghostprofile.py` | record / replay per-application swap-in profiles |
| `sim/` | trace-driven simulator with sourced device latencies |
| `tests/` | real-kernel profile experiment + results |
| `docs/` | [architecture](docs/ARCHITECTURE.md), [research & sources](docs/RESEARCH.md), [safety](docs/SAFETY.md) |

## Prior art (this is not an invention claim)

Borrowing another device's memory (Micron *Memory as a Service*, Nswap, Infiniswap, ReadyBoost) and
history-based prefetching (Windows Prefetcher, TPP, 3PO) all exist. What this repo adds is an open, no-root,
measured combination for ordinary phones and cheap laptops. Details: [docs/RESEARCH.md](docs/RESEARCH.md).

## Roadmap — help wanted

- [ ] **Your measurements**: run `bench/` and open an issue with phone model, cable, USB version, JSON.
- [ ] FunctionFS raw-USB transport (root) for the ~220 µs path.
- [ ] Native (C) NBD server: the Python one adds tens of µs per request.
- [ ] Accuracy gate in `ghostprofile.py` (stop replay when hints are not used).
- [ ] Real-app profiles: llama.cpp, a game level load, a browser session.
- [ ] Windows: user-mode spill library for specific apps (no pagefile).

## License

MIT © 2026 HexaLab

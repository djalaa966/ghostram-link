# Profile replay — real-kernel results (2026-11-21)

Setup: Linux 6.18 (WSL2), swap file on the host SSD, Docker container limited to **96 MB RAM** + 1 GB swap,
`tests/swap_workload.py` allocating **192 MB** and reading every page once in a fixed pseudo-random order
(≈ 46 000 pages come back from swap per sweep). Profile recorded once, then replayed on fresh runs.
Command: `tests/run_profile_experiment.sh 5`.

| run | sweep time (median of 5) | individual runs |
|---|---|---|
| no prefetch (baseline) | **6.38 s** | 6.59 · 6.32 · 6.35 · 6.40 · 6.38 |
| replay `--ahead 1024` | **4.80 s** (−25 %) | 4.92 · 4.84 · 4.80 · 4.61 · 4.67 |
| replay `--ahead 4096` | **2.42 s** (2.6× faster) | 2.53 · 2.20 · 2.42 · 2.21 · 2.45 |
| baseline, other access order | 6.25 s | 6.25 · 6.25 · 6.25 |
| replay with the WRONG profile | 7.18 s (**+15 % slower**) | 7.18 · 7.29 · 7.15 |

What this shows — and what it does not:
- Recorded swap-in order + `process_madvise(MADV_WILLNEED)` from user space works on a stock kernel and
  can hide most of the swap latency **when the app repeats its access order**.
- A wrong profile costs ~15 % (mostly the replay tool scanning `/proc/PID/pagemap` every 50 ms inside a tiny
  96 MB cgroup). An accuracy gate that stops replay when hints are not used is on the roadmap.
- The swap device here is a fast SSD, not a phone. With a phone over USB the latency per page is higher,
  so prefetching matters more — but the bandwidth ceiling of the cable (≈ 35 MB/s USB 2, ≈ 250 MB/s USB 3)
  also caps how much can be hidden. Measure it with `bench/` and share your numbers.
- Synthetic workload. Real apps (games, LLM runtimes, browsers) still need to be tested — help wanted.

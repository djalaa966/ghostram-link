# GhostRAM Link simulator

Trace-driven model of tiered swap on a small laptop: local RAM → zram → (phone RAM over USB) → local disk.

```bash
python ghostsim.py --quick   # ~30 s smoke run
python ghostsim.py           # full matrix (~6 min) → results/results.md + results/results.json
```

- `devices.json`: per-4 KB latency distributions (lognormal median / p99), bandwidth, parallel channels, with the
  source of each number (`measured`, `derived`, `estimate`).
- Workloads: `llm` (weights larger than RAM, read sequentially per token), `browser` (12 tabs, user hops between
  them), `game` (level loads in a fixed order + gameplay hot set), `database` (uniform random),
  `pointer_chase` (fixed linked structure).
- Prefetchers: Linux-like `readahead`, online `stride`, `profile` (fault order recorded on run 1, replayed on
  run 2 with a different seed), optional accuracy `gate`.
- Robustness: cable unplug (with / without disk copy), graceful revoke by the phone (write-back drain),
  thermal throttling (static vs adaptive tier demotion).

This is a model, not a measurement. The USB rows are derived from published pings and USB SSD benchmarks;
the first real step is a FunctionFS ping-pong benchmark on a phone.

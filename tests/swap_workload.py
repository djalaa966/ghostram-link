#!/usr/bin/env python3
"""Test workload for ghostprofile: allocate more memory than the cgroup allows (so part of it is swapped),
then sweep it in a fixed pseudo-random page order N times and print how long each sweep took.
    python3 swap_workload.py --mb 192 --sweeps 3 --wait 3
"""
import argparse, mmap, random, time
ap = argparse.ArgumentParser(); ap.add_argument('--mb', type=int, default=192); ap.add_argument('--sweeps', type=int, default=3)
ap.add_argument('--wait', type=float, default=3, help='seconds to wait before sweeping (attach record/replay)')
ap.add_argument('--order-seed', type=int, default=42, help='access order; change it to simulate an app that does NOT repeat itself')
a = ap.parse_args()
P = 4096; n = a.mb * (1 << 20) // P
m = mmap.mmap(-1, n * P, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
for i in range(n): m[i * P] = i % 251          # touch everything: the cgroup limit pushes the oldest pages to swap
order = list(range(n)); random.Random(a.order_seed).shuffle(order)   # same "access pattern" on every run (like a level load)
print('ready', flush=True); time.sleep(a.wait)
for s in range(a.sweeps):
    t = time.perf_counter(); acc = 0
    for i in order: acc += m[i * P]
    print(f'sweep {s}: {time.perf_counter() - t:.3f} s', flush=True)

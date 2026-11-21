#!/usr/bin/env python3
"""
GhostRAM Link benchmark — PC side. Measures how long it takes to fetch one 4 KB page from the phone's RAM.
Works on Linux, macOS and Windows (Python 3.8+, no dependencies).

    adb forward tcp:10810 tcp:10810         # (USB) — or use the phone's tethering IP with --host
    python page_client.py                   # 5 000 random reads at queue depth 1
    python page_client.py --qd 8            # 8 parallel streams (how much the link overlaps)
    python page_client.py --write           # 4 KB writes instead of reads
    python page_client.py --json out.json   # also save raw numbers (please share them in an issue!)

Prints median / p90 / p99 / max latency in microseconds and the throughput, plus a comparison with
published reference numbers (eMMC, NVMe) so you can see where your phone + cable land.
"""
import argparse, json, platform, random, socket, statistics, struct, threading, time

PAGE = 4096
REQ = struct.Struct('>IQ')
REFERENCE_US = {  # median latency of one 4 KB random read, from public benchmarks (see docs/RESEARCH.md)
    'DRAM': 0.1, 'zram (LZ4)': 3, 'NVMe Gen4 (980 Pro)': 49, 'NVMe Gen3 (970 EVO Plus)': 83,
    'USB 3 SSD (Samsung T7)': 190, 'laptop eMMC (SanDisk DF4032)': 414,
}


def recv_exact(sock, n):
    buf = bytearray(n); view = memoryview(buf); got = 0
    while got < n:
        k = sock.recv_into(view[got:], n - got)
        if not k: raise ConnectionError('server closed the connection')
        got += k
    return buf


def worker(host, port, n, write, pages, out, seed):
    rng = random.Random(seed)
    s = socket.create_connection((host, port)); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    payload = bytes(PAGE); lat = []
    try:
        for i in range(n):
            page = rng.randrange(pages)
            t0 = time.perf_counter_ns()
            if write:
                s.sendall(REQ.pack(1, page) + payload); recv_exact(s, 1)
            else:
                s.sendall(REQ.pack(0, page)); recv_exact(s, PAGE)
            lat.append((time.perf_counter_ns() - t0) / 1000)
    finally:
        s.close()
    out.extend(lat)


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=10810)
    ap.add_argument('-n', type=int, default=5000, help='requests per stream')
    ap.add_argument('--qd', type=int, default=1, help='parallel streams (queue depth)')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--mb', type=int, default=256, help='must match the server --mb')
    ap.add_argument('--warmup', type=int, default=200)
    ap.add_argument('--json', help='save raw results to this file')
    ap.add_argument('--label', default='', help='e.g. "Pixel 7, USB 3 cable, adb forward"')
    a = ap.parse_args()
    pages = a.mb * (1 << 20) // PAGE

    warm = []; worker(a.host, a.port, a.warmup, a.write, pages, warm, 1)
    lat = []; threads = []
    t0 = time.perf_counter()
    for k in range(a.qd):
        th = threading.Thread(target=worker, args=(a.host, a.port, a.n, a.write, pages, lat, 100 + k)); th.start(); threads.append(th)
    for th in threads: th.join()
    wall = time.perf_counter() - t0
    v = sorted(lat)
    res = {
        'label': a.label, 'op': 'write' if a.write else 'read', 'qd': a.qd, 'requests': len(v),
        'median_us': round(statistics.median(v), 1), 'p90_us': round(pct(v, .90), 1), 'p99_us': round(pct(v, .99), 1),
        'max_us': round(v[-1], 1), 'mean_us': round(statistics.fmean(v), 1),
        'throughput_MBps': round(len(v) * PAGE / wall / 1e6, 1), 'pages_per_s': round(len(v) / wall),
        'host_os': platform.platform(), 'python': platform.python_version(),
    }
    print(f"\n4 KB {res['op']}s over {a.host}:{a.port}  (queue depth {a.qd}, {len(v)} requests)")
    print(f"  median {res['median_us']} µs   p90 {res['p90_us']} µs   p99 {res['p99_us']} µs   max {res['max_us']} µs")
    print(f"  throughput {res['throughput_MBps']} MB/s ({res['pages_per_s']} pages/s)\n")
    print('Where this lands (median, 4 KB random read, public benchmarks):')
    rows = sorted(list(REFERENCE_US.items()) + [('→ your phone', res['median_us'])], key=lambda x: x[1])
    for name, us in rows:
        bar = '█' * max(1, min(50, int(us / 10)))
        print(f'  {name:<30} {us:>9.1f} µs  {bar}')
    if a.json:
        res['latencies_us'] = [round(x, 1) for x in lat]
        with open(a.json, 'w') as f: json.dump(res, f)
        print(f'\nsaved {a.json} — share it in a GitHub issue so everyone can compare phones and cables.')


if __name__ == '__main__':
    main()

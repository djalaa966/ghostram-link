#!/usr/bin/env python3
"""
GhostRAM Link — trace-driven simulator of tiered swap on a small laptop.

Question it answers: if a laptop runs out of RAM, how much slower does a workload get when the
overflow goes to (a) local eMMC / NVMe / HDD swap, (b) zram, or (c) the RAM of a phone reached over
USB — with and without prefetching (Linux-like readahead, online stride detection, recorded
per-application profiles), and what happens when the cable is pulled or the phone heats up.

It is a MODEL, not a measurement: device latencies are lognormal distributions whose median/p99 come
from devices.json (sourced or marked "estimate"). Everything is deterministic (seeded).

    python ghostsim.py                 # full matrix → results/results.json + results/results.md
    python ghostsim.py --quick         # smaller run for a smoke test
"""
import argparse, heapq, json, math, random
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = 4096

# ───────────────────────────── devices ─────────────────────────────
class Device:
    """A swap device: lognormal per-op latency + `channels` parallel ops + a bandwidth serializer."""
    def __init__(self, name, spec, rng, slow=1.0):
        self.name, self.rng, self.slow = name, rng, slow
        self.r_med, self.r_p99 = spec['read_us']
        self.w_med, self.w_p99 = spec.get('write_us', spec['read_us'])
        self.bw = spec['bw_MBps'] * 1e6 / 1e6          # bytes per µs
        self.chan = [0.0] * spec.get('channels', 1)
        self.bw_free = 0.0
        self.reads = self.writes = 0
        self.lat_log = []                              # observed demand-read latencies (for adaptive policy)

    def _draw(self, med, p99):
        sigma = math.log(p99 / med) / 2.326
        return med * math.exp(self.rng.gauss(0, sigma)) * self.slow

    def op(self, now, write=False):
        i = min(range(len(self.chan)), key=self.chan.__getitem__)
        start = max(now, self.chan[i])
        xfer = PAGE / self.bw
        slot = max(start, self.bw_free)
        self.bw_free = slot + xfer
        lat = self._draw(self.w_med, self.w_p99) if write else self._draw(self.r_med, self.r_p99)
        done = max(start + lat, slot + xfer)
        self.chan[i] = done
        if write: self.writes += 1
        else: self.reads += 1
        return done


# ───────────────────────────── workloads ─────────────────────────────
# Each returns a list of page numbers touched in order. Sizes are scaled: RAM = 4096 pages (16 MB);
# the shapes, not the absolute sizes, are what matter.
def wl_llm(rng, n):           # model weights bigger than RAM, read sequentially once per token
    W = 9000; out = []
    while len(out) < n: out.extend(range(W))
    return out[:n]

def wl_browser(rng, n):       # 12 tabs; user hops between them; each visit replays the tab's pages in
    tabs = [list(range(t * 1100, t * 1100 + 1100)) for t in range(12)]   # (mostly) the same order
    for t in tabs: rng.shuffle(t)
    out, hot = [], 0
    while len(out) < n:
        hot = rng.randrange(12) if rng.random() < 0.7 else hot
        seq = tabs[hot][: rng.randint(300, 1100)]
        out.extend(p if rng.random() > 0.05 else rng.randrange(13200) for p in seq)
    return out[:n]

def wl_game(rng, n):          # levels: each load touches its assets in a fixed order, then plays
    levels = [list(range(l * 2500, l * 2500 + 2500)) for l in range(6)]  # (random within the level)
    for lv in levels: rng.shuffle(lv)
    out = []
    while len(out) < n:
        lv = levels[rng.randrange(6)]
        out.extend(lv)                                  # loading screen
        out.extend(rng.choice(lv[:800]) for _ in range(3000))   # gameplay: hot subset
    return out[:n]

def wl_db(rng, n):            # uniform random over a table 3× RAM
    return [rng.randrange(12500) for _ in range(n)]

def wl_chase(rng, n):         # pointer chasing over a fixed linked structure (same chain every run)
    perm = list(range(11000)); random.Random(7).shuffle(perm)   # structure fixed across runs
    out, p = [], rng.randrange(11000)
    while len(out) < n: out.append(p); p = perm[p]
    return out[:n]

WORKLOADS = {'llm': wl_llm, 'browser': wl_browser, 'game': wl_game, 'database': wl_db, 'pointer_chase': wl_chase}


# ───────────────────────────── simulator ─────────────────────────────
class Sim:
    def __init__(self, cfg, devs, seed):
        self.cfg, self.rng = cfg, random.Random(seed)
        self.ram_pages = cfg['ram_pages']
        self.zram_cap = cfg.get('zram_pages', 0)
        self.lru_cap = self.ram_pages - int(self.zram_cap / cfg.get('zram_ratio', 2.5))
        self.host = OrderedDict()                       # resident pages (LRU)
        self.zram = OrderedDict()                       # compressed pages (LRU)
        self.where = {}                                 # page -> 'far' | 'disk' (tier holding it)
        self.on_disk = set()                            # pages that also have a disk copy (write-through)
        self.far = Device('far', devs[cfg['far']], random.Random(seed + 1)) if cfg.get('far') else None
        self.disk = Device('disk', devs[cfg['disk']], random.Random(seed + 2))
        self.zdec = devs['zram']['read_us'][0]; self.zcomp = devs['zram'].get('write_us', [6])[0]
        self.inflight = {}                              # page -> arrival time
        self.arrivals = []                              # heap (time, page)
        self.now = 0.0
        self.stall = 0.0
        self.faults = {'zram': 0, 'far': 0, 'disk': 0, 'inflight_hit': 0}
        self.prefetch_issued = self.prefetch_used = 0
        self.prefetched = set()
        self.lost = 0
        self.far_alive = self.far is not None
        self.far_bad = False                            # adaptive policy demoted the far tier
        self.recorded = []                              # fault order (for profile recording)
        self.profile = None; self.pidx = {}
        self.last = []                                  # recent faults (stride detector)
        self.gate_issued = self.gate_used = 0           # accuracy gate (window counters)
        self.gate_off_until = -1                        # access index until which prefetch is paused
        self.idx = 0
        self.drain_ms = None

    # -- placement of an evicted page
    def _evict_one(self):
        page, _ = self.host.popitem(last=False)
        if self.zram_cap:
            self.now += self.zcomp * 0.0                # compression is mostly async (kswapd); not on the critical path
            self.zram[page] = True
            if len(self.zram) > self.zram_cap:
                p2, _ = self.zram.popitem(last=False)
                self._to_backing(p2)
        else:
            self._to_backing(page)

    def _to_backing(self, page):
        use_far = self.far_alive and not self.far_bad
        if use_far:
            self.far.op(self.now, write=True)
            self.where[page] = 'far'
            if self.cfg.get('write_through'):
                if page not in self.on_disk: self.disk.op(self.now, write=True); self.on_disk.add(page)
        else:
            if page not in self.on_disk: self.disk.op(self.now, write=True); self.on_disk.add(page)
            self.where[page] = 'disk'

    def _insert(self, page):
        self.host[page] = True; self.host.move_to_end(page)
        while len(self.host) > self.lru_cap: self._evict_one()

    def _land_arrivals(self):
        while self.arrivals and self.arrivals[0][0] <= self.now:
            t, p = heapq.heappop(self.arrivals)
            if self.inflight.pop(p, None) is not None and p not in self.host:
                self._insert(p)

    # -- fetch from backing tiers
    def _fetch(self, page, demand):
        tier = self.where.get(page)
        if tier == 'far' and not self.far_alive:
            if page in self.on_disk: tier = 'disk'
            else:
                self.lost += 1; tier = 'disk'          # data lost: counted, then treated as a re-read
        dev = self.far if tier == 'far' else self.disk
        t0 = self.now
        done = dev.op(self.now)
        if demand and tier == 'far': dev.lat_log.append(done - t0)
        self.where.pop(page, None)
        self.on_disk.discard(page)        # conservative: assume the page may be dirtied once back in RAM
        return tier, done

    def _prefetch(self, page):
        if page in self.host or page in self.inflight or page in self.zram or page not in self.where: return
        if len(self.inflight) >= self.cfg.get('pf_inflight', 64): return
        _, done = self._fetch(page, demand=False)
        self.inflight[page] = done; heapq.heappush(self.arrivals, (done, page))
        self.prefetch_issued += 1; self.gate_issued += 1; self.prefetched.add(page)

    def _gate(self):
        # Accuracy gate: if fewer than 25 % of the last 400 prefetched pages were used, pause prefetching
        # for 5 000 accesses (wrong prefetches waste the slow link and evict useful pages).
        if not self.cfg.get('gate'): return True
        if self.idx < self.gate_off_until: return False
        if self.gate_issued >= 400:
            ok = self.gate_used / self.gate_issued >= 0.25
            self.gate_issued = self.gate_used = 0
            if not ok: self.gate_off_until = self.idx + 5000; return False
        return True

    def _prefetch_after_fault(self, page):
        pf = self.cfg.get('prefetch', 'none')
        if pf != 'none' and not self._gate(): return
        if pf == 'readahead':                           # Linux-like: cluster of neighbours
            for d in range(1, 8): self._prefetch(page + d)
        elif pf == 'stride':                            # online stride detection on fault addresses
            self.last.append(page); self.last = self.last[-4:]
            if len(self.last) >= 3:
                s = self.last[-1] - self.last[-2]
                if s and s == self.last[-2] - self.last[-3]:
                    for k in range(1, 17): self._prefetch(page + s * k)
                else:
                    for d in range(1, 4): self._prefetch(page + d)
        elif pf == 'profile' and self.profile:          # replay the recorded fault order
            i = self.pidx.get(page)
            if i is not None:
                for p in self.profile[i + 1: i + 1 + self.cfg.get('pf_window', 48)]: self._prefetch(p)

    # -- adaptive tier policy (claim f): if the far tier gets slower than the disk, stop using it
    def _adapt(self):
        if not self.cfg.get('adaptive') or not self.far_alive or self.far_bad: return
        log = self.far.lat_log
        if len(log) >= 200 and len(log) % 100 == 0:
            recent = sorted(log[-200:]); p50 = recent[100]
            if p50 > self.cfg['disk_p50_estimate']:
                self.far_bad = True
                # every page already has a disk copy (write-through) → read it from the disk from now on
                for p, tier in list(self.where.items()):
                    if tier == 'far' and p in self.on_disk: self.where[p] = 'disk'

    def run(self, trace, compute_us, events=()):
        events = sorted(events)
        for n, page in enumerate(trace):
            self.idx = n
            while events and events[0][0] <= n:
                _, ev = events.pop(0)
                if ev == 'unplug': self.far_alive = False
                elif ev == 'throttle' and self.far: self.far.slow = 6.0
                elif ev == 'revoke' and self.far_alive:  # graceful: phone asks its memory back (claim: reclaim protocol)
                    last = self.now
                    for p, tier in list(self.where.items()):
                        if tier == 'far':
                            if p not in self.on_disk:
                                self.far.op(self.now); last = max(last, self.disk.op(self.now, write=True)); self.on_disk.add(p)
                            self.where[p] = 'disk'
                    self.drain_ms = round((last - self.now) / 1000, 1)
                    self.far_alive = False
            self.now += compute_us
            self._land_arrivals()
            if page in self.host:
                self.host.move_to_end(page)
                if page in self.prefetched: self.prefetch_used += 1; self.gate_used += 1; self.prefetched.discard(page)
                continue
            if page in self.inflight:                    # prefetch on its way: wait the remainder only
                t = self.inflight.pop(page); self.faults['inflight_hit'] += 1
                if t > self.now: self.stall += t - self.now; self.now = t
                self.prefetch_used += 1; self.gate_used += 1; self.prefetched.discard(page)
                self._insert(page); continue
            self.recorded.append(page)
            if page in self.zram:
                self.zram.pop(page); self.faults['zram'] += 1
                self.stall += self.zdec; self.now += self.zdec
                self._insert(page); self._prefetch_after_fault(page); continue
            if page in self.where:
                tier, done = self._fetch(page, demand=True)
                self.faults['far' if tier == 'far' else 'disk'] += 1
                if done > self.now: self.stall += done - self.now; self.now = done
                self._insert(page); self._prefetch_after_fault(page); self._adapt(); continue
            self._insert(page)                          # first touch: zero-fill, no I/O
        return self


CONFIGS = {
    # name: description, far device, disk device, zram, prefetch, extras
    'emmc_swap':          dict(far=None, disk='emmc', zram=False),
    'zram+emmc':          dict(far=None, disk='emmc', zram=True),
    'zram+phone_usb2':    dict(far='phone_usb2', disk='emmc', zram=True, write_through=True),
    'zram+phone_usb3':    dict(far='phone_usb3', disk='emmc', zram=True, write_through=True),
    'zram+phone_usb3_ffs':dict(far='phone_usb3_ffs', disk='emmc', zram=True, write_through=True),
    'zram+phone_usb3+readahead': dict(far='phone_usb3', disk='emmc', zram=True, write_through=True, prefetch='readahead'),
    'zram+phone_usb3+stride':    dict(far='phone_usb3', disk='emmc', zram=True, write_through=True, prefetch='stride'),
    'zram+emmc+profile':  dict(far=None, disk='emmc', zram=True, prefetch='profile_disk', gate=True),
    'zram+phone_usb3+profile':   dict(far='phone_usb3', disk='emmc', zram=True, write_through=True, prefetch='profile'),
    'zram+phone_usb3+profile+gate': dict(far='phone_usb3', disk='emmc', zram=True, write_through=True, prefetch='profile', gate=True),
    'nvme_swap':          dict(far=None, disk='nvme', zram=False),
    'zram+nvme':          dict(far=None, disk='nvme', zram=True),
    'zram+nvme+profile':  dict(far=None, disk='nvme', zram=True, prefetch='profile_disk'),
    'zram+nvme+profile+gate': dict(far=None, disk='nvme', zram=True, prefetch='profile_disk', gate=True),
    'hdd_swap':           dict(far=None, disk='hdd', zram=False),
    'zram+hdd':           dict(far=None, disk='hdd', zram=True),
    'zram+hdd+profile+gate': dict(far=None, disk='hdd', zram=True, prefetch='profile_disk', gate=True),
    'zram+phone_usb3+profile+gate (hdd laptop)': dict(far='phone_usb3', disk='hdd', zram=True, write_through=True, prefetch='profile', gate=True),
}


def build_cfg(c, base):
    cfg = dict(ram_pages=base['ram_pages'], far=c.get('far'), disk=c['disk'], write_through=c.get('write_through', False), gate=c.get('gate', False),
               prefetch=c.get('prefetch', 'none'), pf_window=base['pf_window'], pf_inflight=base['pf_inflight'])
    if c.get('zram'): cfg.update(zram_pages=base['zram_pages'], zram_ratio=base['zram_ratio'])
    return cfg


def simulate(cfg, devs, trace_rec, trace_run, compute_us, seed, events=()):
    """Profile prefetch: record on run 1 (trace_rec), replay on run 2 (trace_run, different seed)."""
    profile = None
    pf = cfg['prefetch']
    if pf in ('profile', 'profile_disk'):
        rec = Sim(dict(cfg, prefetch='none', gate=False), devs, seed).run(trace_rec, compute_us)
        profile = rec.recorded
        cfg = dict(cfg, prefetch='profile')
    s = Sim(cfg, devs, seed + 100)
    if profile:
        s.profile = profile; s.pidx = {}
        for i, p in enumerate(profile): s.pidx.setdefault(p, i)
    # profile_disk: prefetch from the local disk (no far tier) — the "profiles alone" baseline
    s.run(trace_run, compute_us, events)
    ideal = len(trace_run) * compute_us
    return {
        'slowdown': round(s.now / ideal, 3),
        'stall_ms': round(s.stall / 1000, 1),
        'faults': s.faults,
        'prefetch_issued': s.prefetch_issued,
        'prefetch_accuracy': round(s.prefetch_used / s.prefetch_issued, 3) if s.prefetch_issued else None,
        'disk_writes': s.disk.writes, 'disk_reads': s.disk.reads,
        'far_reads': s.far.reads if s.far else 0, 'far_writes': s.far.writes if s.far else 0,
        'lost_pages': s.lost, 'far_demoted': s.far_bad, 'drain_ms': s.drain_ms,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--n', type=int, default=0)
    args = ap.parse_args()
    devs = json.loads((HERE / 'devices.json').read_text(encoding='utf-8'))['devices']
    base = dict(ram_pages=4096, zram_pages=4096, zram_ratio=2.5, pf_window=48, pf_inflight=64)
    n = args.n or (40_000 if args.quick else 200_000)
    compute_us = 2.0                                    # CPU work per page touched (all-in-RAM speed)
    out = {'params': dict(base, accesses=n, compute_us=compute_us), 'matrix': {}, 'events': {}}

    for wname, gen in WORKLOADS.items():
        t_rec = gen(random.Random(11), n); t_run = gen(random.Random(22), n)
        out['matrix'][wname] = {}
        for cname, c in CONFIGS.items():
            out['matrix'][wname][cname] = simulate(build_cfg(c, base), devs, t_rec, t_run, compute_us, seed=5)
        print('ok', wname)

    # Robustness scenarios on the browser workload, phone over USB3 + profile prefetch
    t_rec = wl_browser(random.Random(11), n); t_run = wl_browser(random.Random(22), n)
    disk_p50 = devs['emmc']['read_us'][0]
    scen = {
        'unplug_no_backup':      (dict(CONFIGS['zram+phone_usb3+profile'], write_through=False), [(n // 2, 'unplug')]),
        'unplug_write_through':  (CONFIGS['zram+phone_usb3+profile'], [(n // 2, 'unplug')]),
        'revoke_write_back':     (dict(CONFIGS['zram+phone_usb3+profile+gate'], write_through=False), [(n // 2, 'revoke')]),
        'revoke_write_through':  (CONFIGS['zram+phone_usb3+profile+gate'], [(n // 2, 'revoke')]),
        'no_event_write_back':   (dict(CONFIGS['zram+phone_usb3+profile+gate'], write_through=False), []),
        'no_event_write_through':(CONFIGS['zram+phone_usb3+profile+gate'], []),
        'throttle_static':       (CONFIGS['zram+phone_usb3+profile'], [(n // 3, 'throttle')]),
        'throttle_adaptive':     (dict(CONFIGS['zram+phone_usb3+profile'], adaptive=True), [(n // 3, 'throttle')]),
    }
    for sname, (c, ev) in scen.items():
        cfg = build_cfg(c, base); cfg.update(adaptive=c.get('adaptive', False), disk_p50_estimate=disk_p50)
        if not c.get('write_through', True): cfg['write_through'] = False
        out['events'][sname] = simulate(cfg, devs, t_rec, t_run, compute_us, seed=5, events=ev)
        print('ok', sname)

    res = HERE / 'results'; res.mkdir(exist_ok=True)
    (res / 'results.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    # markdown summary
    lines = ['# GhostRAM Link — simulation results', '',
             f"RAM {base['ram_pages']} pages, zram {base['zram_pages']} pages (ratio {base['zram_ratio']}), {n} accesses, {compute_us} µs CPU per page. "
             'Slowdown = runtime ÷ runtime with unlimited RAM (1.0 = no penalty). Profile = recorded on run 1, replayed on run 2 with a different seed.', '']
    cols = list(CONFIGS)
    lines.append('| config | ' + ' | '.join(WORKLOADS) + ' |'); lines.append('|---' * (len(WORKLOADS) + 1) + '|')
    for c in cols:
        lines.append(f'| {c} | ' + ' | '.join(f"{out['matrix'][w][c]['slowdown']}×" for w in WORKLOADS) + ' |')
    lines += ['', '## Prefetch accuracy (share of prefetched pages actually used)', '']
    lines.append('| config | ' + ' | '.join(WORKLOADS) + ' |'); lines.append('|---' * (len(WORKLOADS) + 1) + '|')
    for c in cols:
        if CONFIGS[c].get('prefetch'):
            lines.append(f'| {c} | ' + ' | '.join(str(out['matrix'][w][c]['prefetch_accuracy']) for w in WORKLOADS) + ' |')
    lines += ['', '## Robustness (browser workload, phone USB3 + profile)', '', '| scenario | slowdown | lost pages | disk writes | drain (ms) | far tier demoted |', '|---|---|---|---|---|---|']
    for s, r in out['events'].items():
        lines.append(f"| {s} | {r['slowdown']}× | {r['lost_pages']} | {r['disk_writes']} | {r['drain_ms']} | {r['far_demoted']} |")
    (res / 'results.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    import sys; sys.stdout.reconfigure(encoding='utf-8'); print((res / 'results.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
GhostRAM Link — per-application prefetch profiles (Linux, EXPERIMENTAL).

Idea: many heavy apps touch their memory in the same order every time (a game loading a level, an LLM
runtime streaming its weights, a browser restoring tabs). `record` watches one run and writes down the
order in which the app's swapped-out pages came back. `replay` follows a later run and asks the kernel to
bring the NEXT pages of that order back from swap (phone tier, zram or disk) before the app needs them.

    sudo python3 ghostprofile.py record --pid 4242 --out game.ghostprofile --seconds 120
    sudo python3 ghostprofile.py replay --pid 5151 --profile game.ghostprofile

How it works (no kernel module, no eBPF):
  • record: samples /proc/PID/pagemap; a page whose entry flips from "swapped" (bit 62) to "present"
    (bit 63) between two samples was faulted back in → appended to the profile in that order.
  • pages are keyed by (mapping, offset inside the mapping), not by absolute address, so a profile
    survives ASLR and can be reused on later runs.
  • replay: process_madvise(pidfd, …, MADV_WILLNEED) on the next `--ahead` pages of the profile, moving
    the cursor forward as the app catches up. Needs Linux ≥ 5.15, ptrace permission on the target and
    CAP_SYS_NICE (in practice: run with sudo).

Limits (be honest in your measurements):
  • sampling misses pages that are swapped in and out again between two samples;
  • anonymous mappings are matched by their order and size — an app that maps memory differently on
    each run will match poorly (replay then simply does nothing useful);
  • scanning pagemap costs CPU on very large processes; raise --interval if needed.
"""
import argparse, ctypes, json, os, struct, sys, time
from array import array

PAGE = os.sysconf('SC_PAGE_SIZE') if hasattr(os, 'sysconf') else 4096
SYS_PIDFD_OPEN, SYS_PROCESS_MADVISE = 434, 440      # same numbers on x86_64 and arm64 (generic table)
MADV_WILLNEED = 3
IOV_MAX = 1024
PRESENT, SWAPPED = 1 << 63, 1 << 62


class IOVec(ctypes.Structure):
    _fields_ = [('iov_base', ctypes.c_void_p), ('iov_len', ctypes.c_size_t)]


def regions(pid):
    """Private writable mappings (anonymous memory, heap, stack) — what can be swapped."""
    out, anon_idx = [], {}
    with open(f'/proc/{pid}/maps') as f:
        for line in f:
            parts = line.split(None, 5)
            addr, perms = parts[0], parts[1]
            path = parts[5].strip() if len(parts) > 5 else ''
            if 'w' not in perms or perms[3] != 'p': continue
            if path and not path.startswith('['): continue          # file-backed: page cache, not swap
            start, end = (int(x, 16) for x in addr.split('-'))
            kind = path or 'anon'
            size = end - start
            n = anon_idx.get((kind, size), 0); anon_idx[(kind, size)] = n + 1
            out.append((f'{kind}:{size}:{n}', start, end))           # stable-ish key across runs
    return out


def scan(pid, regs):
    """Yield (key, page_index, entry) for every page of the given regions."""
    with open(f'/proc/{pid}/pagemap', 'rb') as pm:
        for key, start, end in regs:
            pm.seek(start // PAGE * 8)
            raw = pm.read((end - start) // PAGE * 8)
            entries = array('Q'); entries.frombytes(raw[: len(raw) // 8 * 8])
            if sys.byteorder != 'little': entries.byteswap()
            yield key, start, entries


def record(a):
    swapped_before = {}                                   # (key, idx) → True if swapped at last sample
    order, seen = [], set()
    t_end = time.time() + a.seconds
    samples = 0
    while time.time() < t_end:
        try:
            regs = regions(a.pid)
            now = {}
            for key, start, entries in scan(a.pid, regs):
                for i, e in enumerate(entries):
                    if e & SWAPPED: now[(key, i)] = True
                    elif e & PRESENT and swapped_before.get((key, i)) and (key, i) not in seen:
                        order.append([key, i]); seen.add((key, i))
            swapped_before = now
        except (FileNotFoundError, ProcessLookupError):
            print('process exited'); break
        samples += 1
        time.sleep(a.interval)
    prof = {'format': 'ghostprofile/1', 'page_size': PAGE, 'samples': samples,
            'interval_s': a.interval, 'note': a.note, 'pages': order}
    with open(a.out, 'w') as f: json.dump(prof, f)
    print(f'recorded {len(order)} swap-ins in {samples} samples → {a.out}')


def process_madvise(pidfd, ranges):
    libc = ctypes.CDLL(None, use_errno=True)
    done = 0
    for i in range(0, len(ranges), IOV_MAX):
        chunk = ranges[i:i + IOV_MAX]
        iov = (IOVec * len(chunk))(*[IOVec(addr, ln) for addr, ln in chunk])
        r = libc.syscall(SYS_PROCESS_MADVISE, pidfd, iov, len(chunk), MADV_WILLNEED, 0)
        if r < 0:
            err = ctypes.get_errno()
            raise OSError(err, f'process_madvise failed: {os.strerror(err)} (needs Linux ≥ 5.15, ptrace permission and CAP_SYS_NICE — run with sudo)')
        done += r
    return done


def match_regions(prof_keys, current):
    """For each profile region, the current mapping(s) it may live in, with page shift candidates.

    The kernel merges adjacent anonymous mappings, so the same allocation can show up slightly bigger
    (or smaller) on another run, with the extra pages BEFORE or AFTER it. We therefore keep up to two
    alignments per region — recorded region at the start (shift 0) or at the end (shift = size delta) —
    and let replay() pick the one whose swap-ins follow the profile order.
    Returns {profile key: [(start_address, current_pages, shift_pages), ...]}."""
    by_key = {k: (s, e) for k, s, e in current}
    out, used = {}, set()
    for pk in prof_keys:
        if pk in by_key:
            s0, e0 = by_key[pk]; out[pk] = [(s0, (e0 - s0) // PAGE, 0)]; used.add(pk)
    for pk in prof_keys:
        if pk in out: continue
        kind, size = pk.rsplit(':', 2)[0], int(pk.rsplit(':', 2)[1])
        cands = [(abs((e0 - s0) - size), k, s0, e0) for k, s0, e0 in current
                 if k not in used and k.rsplit(':', 2)[0] == kind
                 and size * 0.75 - (2 << 20) <= e0 - s0 <= size * 1.25 + (2 << 20)]
        if not cands: continue
        _, k, s0, e0 = min(cands); used.add(k)
        cur_pages, rec_pages = (e0 - s0) // PAGE, size // PAGE
        shifts = sorted({0, cur_pages - rec_pages})
        out[pk] = [(s0, cur_pages, sh) for sh in shifts]
    return out


def replay(a):
    with open(a.profile) as f: prof = json.load(f)
    if prof.get('page_size') != PAGE: sys.exit('profile recorded with a different page size')
    libc = ctypes.CDLL(None, use_errno=True)
    pidfd = libc.syscall(SYS_PIDFD_OPEN, a.pid, 0)
    if pidfd < 0: sys.exit(f'pidfd_open failed: {os.strerror(ctypes.get_errno())}')
    pages = [tuple(p) for p in prof['pages']]
    pos = {p: i for i, p in enumerate(pages)}
    prof_keys = sorted({k for k, _ in pages})
    cursor, issued, total_bytes = 0, 0, 0
    locked = {}                                   # profile key → chosen shift
    score = {}                                    # (profile key, shift) → local swap-ins seen
    swapped_prev = {}                             # (profile key, shift) → set of page indexes
    hinted = {}                                   # page → time of the last hint (avoid re-asking)
    print(f'replaying {len(pages)} pages, {a.ahead} ahead of the app')
    t_end = time.time() + a.seconds
    try:
        while time.time() < t_end and cursor < len(pages):
            t_loop = time.time()
            cands = match_regions(prof_keys, regions(a.pid))
            back_all, swapped_now, addr = [], set(), {}
            with open(f'/proc/{a.pid}/pagemap', 'rb') as pm:
                for pk, options in cands.items():
                    s0, cur_pages, _ = options[0]
                    pm.seek(s0 // PAGE * 8)
                    entries = array('Q'); raw = pm.read(cur_pages * 8)
                    entries.frombytes(raw[: len(raw) // 8 * 8])
                    if sys.byteorder != 'little': entries.byteswap()
                    swp_idx = [j for j, e in enumerate(entries) if e & SWAPPED]
                    prs_idx = [j for j, e in enumerate(entries) if e & PRESENT]
                    for _, _, sh in options:
                        if pk in locked and locked[pk] != sh: continue
                        sw = {j - sh for j in swp_idx}
                        pr = {j - sh for j in prs_idx}
                        prev = swapped_prev.get((pk, sh), set())
                        back = [pos[(pk, i)] for i in (pr & prev) if (pk, i) in pos]
                        swapped_prev[(pk, sh)] = sw
                        if pk not in locked:
                            # Swap-ins that land just ahead of the cursor = this alignment follows the profile.
                            score[(pk, sh)] = score.get((pk, sh), 0) + sum(cursor <= x < cursor + a.ahead for x in back)
                            continue
                        back_all += back
                        swapped_now |= {(pk, i) for i in sw}
                        addr[pk] = (s0, sh)
                    if pk not in locked:
                        sc = sorted(((score.get((pk, sh), 0), sh) for _, _, sh in options), reverse=True)
                        best, second = sc[0][0], (sc[1][0] if len(sc) > 1 else 0)
                        if len(options) == 1 or (best >= 32 and best >= 3 * max(second, 1)):
                            locked[pk] = sc[0][1]
                            if a.verbose: print(f'locked {pk} shift {sc[0][1]} (scores {sc})', file=sys.stderr, flush=True)
            # Where is the app in its recorded order? Use the pages that JUST came back from swap, and only
            # those close to the cursor: the kernel's own swap readahead also brings back neighbours in
            # swap-slot order, whose profile positions are scattered.
            back_all.sort()
            local = [x for x in back_all if cursor <= x < cursor + 4 * a.ahead]
            if len(local) >= 8:
                cursor = max(cursor, local[len(local) // 2])
            elif len(back_all) >= 64 and not local:                 # lost track (app skipped ahead): resync
                cursor = max(cursor, back_all[len(back_all) // 2])
            now = time.time()
            window = [p for p in pages[cursor: cursor + a.ahead]
                      if p in swapped_now and p[0] in addr and now - hinted.get(p, 0) > 1.0]
            ranges = [(addr[k][0] + (i + addr[k][1]) * PAGE, PAGE) for k, i in window]
            t_adv = time.time()
            if ranges:
                total_bytes += process_madvise(pidfd, ranges); issued += len(ranges)
                for p in window: hinted[p] = now
            if a.verbose:
                print(f'{now:.2f} scan {t_adv - t_loop:.3f}s madvise {time.time() - t_adv:.3f}s '
                      f'back {len(back_all)} local {len(local)} cursor {cursor} hints {len(ranges)}',
                      file=sys.stderr, flush=True)
            time.sleep(a.interval)
    except (FileNotFoundError, ProcessLookupError):
        print('process exited')
    print(f'replay done: cursor {cursor}/{len(pages)}, {issued} prefetch hints, {total_bytes >> 20} MB requested')


def main():
    ap = argparse.ArgumentParser(description='Record / replay per-application swap-in profiles.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('record'); r.add_argument('--pid', type=int, required=True); r.add_argument('--out', required=True)
    r.add_argument('--seconds', type=float, default=60); r.add_argument('--interval', type=float, default=0.05)
    r.add_argument('--note', default='', help='app, version, workload — shared with the profile')
    p = sub.add_parser('replay'); p.add_argument('--pid', type=int, required=True); p.add_argument('--profile', required=True)
    p.add_argument('--ahead', type=int, default=256, help='pages to prefetch ahead of the app')
    p.add_argument('--seconds', type=float, default=600); p.add_argument('--interval', type=float, default=0.05)
    p.add_argument('--verbose', action='store_true', help='print one line per iteration (stderr)')
    a = ap.parse_args()
    if not sys.platform.startswith('linux'): sys.exit('Linux only')
    record(a) if a.cmd == 'record' else replay(a)


if __name__ == '__main__':
    main()

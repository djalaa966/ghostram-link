#!/usr/bin/env python3
"""
GhostRAM Link — phone side: serve a block of the phone's RAM as an NBD (Network Block Device) export.

The PC connects with the standard Linux `nbd-client` (over `adb forward` or USB tethering), encrypts the
device with dm-crypt and uses it as a swap tier. The phone only ever stores ciphertext.

Runs anywhere Python 3.8+ runs: Termux on Android (no root needed), a Raspberry Pi, a Linux box.

    python3 ghostram_nbd.py --size 2G                 # listen on 127.0.0.1:10809 (use with `adb forward`)
    python3 ghostram_nbd.py --size 2G --bind 0.0.0.0  # USB tethering (see docs/SAFETY.md first)

Memory is an anonymous mmap: pages are only allocated by Android when the PC writes them, and TRIM
(discard) gives them back. Protocol: NBD fixed-newstyle handshake with NBD_OPT_GO / INFO / EXPORT_NAME
/ LIST / ABORT, simple replies, commands READ / WRITE / FLUSH / TRIM / DISC.
Spec: https://github.com/NetworkBlockDevice/nbd/blob/master/doc/proto.md
"""
import argparse, mmap, os, signal, socket, struct, sys, time

NBDMAGIC = 0x4E42444D41474943
IHAVEOPT = 0x49484156454F5054
REPLY_MAGIC_OPT = 0x3E889045565A9
REQ_MAGIC = 0x25609513
SIMPLE_REPLY_MAGIC = 0x67446698

# handshake flags
FLAG_FIXED_NEWSTYLE, FLAG_NO_ZEROES = 1 << 0, 1 << 1
# options
OPT_EXPORT_NAME, OPT_ABORT, OPT_LIST, OPT_INFO, OPT_GO = 1, 2, 3, 6, 7
# option replies
REP_ACK, REP_SERVER, REP_INFO = 1, 2, 3
REP_ERR_UNSUP, REP_ERR_INVALID = (1 << 31) + 1, (1 << 31) + 3
# info types
INFO_EXPORT, INFO_BLOCK_SIZE = 0, 3
# transmission flags
TF_HAS_FLAGS, TF_SEND_FLUSH, TF_SEND_TRIM = 1 << 0, 1 << 2, 1 << 5
# commands
CMD_READ, CMD_WRITE, CMD_DISC, CMD_FLUSH, CMD_TRIM = 0, 1, 2, 3, 4
EINVAL, ENOSPC = 22, 28
PAGE = 4096
EXPORT = 'ghostram'


def parse_size(s):
    s = s.strip().upper()
    mult = {'K': 1 << 10, 'M': 1 << 20, 'G': 1 << 30}.get(s[-1:], 1)
    n = int(float(s[:-1] if s[-1:] in 'KMG' else s) * mult)
    return n - n % PAGE


def recv_exact(sock, n):
    buf = bytearray(n); view = memoryview(buf); got = 0
    while got < n:
        k = sock.recv_into(view[got:], n - got)
        if not k: raise ConnectionError('peer closed')
        got += k
    return buf


class Export:
    def __init__(self, size):
        self.size = size
        # Anonymous, zero-filled, allocated lazily by the kernel. MAP_PRIVATE matters: on a shared anonymous
        # mapping (Python's default on Linux) MADV_DONTNEED neither frees nor zeroes the pages.
        if hasattr(mmap, 'MAP_PRIVATE') and hasattr(mmap, 'MAP_ANONYMOUS'):
            self.mem = mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        else:
            self.mem = mmap.mmap(-1, size)
        self.stats = dict(reads=0, writes=0, trims=0, bytes_in=0, bytes_out=0)

    def trim(self, off, length):
        # give whole pages back to the OS (Linux/Android); elsewhere just zero them
        start = (off + PAGE - 1) // PAGE * PAGE
        end = (off + length) // PAGE * PAGE
        if end <= start: return
        if hasattr(self.mem, 'madvise') and hasattr(mmap, 'MADV_DONTNEED'):
            self.mem.madvise(mmap.MADV_DONTNEED, start, end - start)
        else:
            self.mem[start:end] = bytes(end - start)


def opt_reply(sock, opt, rtype, data=b''):
    sock.sendall(struct.pack('>QIII', REPLY_MAGIC_OPT, opt, rtype, len(data)) + data)


def handshake(sock, exp, log):
    """Returns True when the client entered transmission, False if it aborted."""
    sock.sendall(struct.pack('>QQH', NBDMAGIC, IHAVEOPT, FLAG_FIXED_NEWSTYLE | FLAG_NO_ZEROES))
    (cflags,) = struct.unpack('>I', recv_exact(sock, 4))
    no_zeroes = bool(cflags & FLAG_NO_ZEROES)
    tflags = TF_HAS_FLAGS | TF_SEND_FLUSH | TF_SEND_TRIM
    while True:
        magic, opt, length = struct.unpack('>QII', recv_exact(sock, 16))
        if magic != IHAVEOPT: raise ConnectionError('bad option magic')
        if length > 1 << 16: raise ConnectionError('option too long')
        data = bytes(recv_exact(sock, length)) if length else b''
        if opt == OPT_EXPORT_NAME:                  # old style: no reply header, straight into transmission
            sock.sendall(struct.pack('>QH', exp.size, tflags) + (b'' if no_zeroes else bytes(124)))
            return True
        if opt == OPT_ABORT:
            opt_reply(sock, opt, REP_ACK); return False
        if opt == OPT_LIST:
            name = EXPORT.encode()
            opt_reply(sock, opt, REP_SERVER, struct.pack('>I', len(name)) + name)
            opt_reply(sock, opt, REP_ACK); continue
        if opt in (OPT_INFO, OPT_GO):
            if length < 6:
                opt_reply(sock, opt, REP_ERR_INVALID); continue
            opt_reply(sock, opt, REP_INFO, struct.pack('>HQH', INFO_EXPORT, exp.size, tflags))
            opt_reply(sock, opt, REP_INFO, struct.pack('>HIII', INFO_BLOCK_SIZE, 512, PAGE, 32 << 20))
            opt_reply(sock, opt, REP_ACK)
            if opt == OPT_GO: return True
            continue
        log(f'unsupported option {opt}')
        opt_reply(sock, opt, REP_ERR_UNSUP)


def serve(sock, exp, log):
    reply = struct.Struct('>IIQ')
    req = struct.Struct('>IHHQQI')
    while True:
        magic, _cflags, ctype, handle, off, length = req.unpack(recv_exact(sock, 28))
        if magic != REQ_MAGIC: raise ConnectionError('bad request magic')
        bad = off + length > exp.size
        if ctype == CMD_READ:
            if bad:
                sock.sendall(reply.pack(SIMPLE_REPLY_MAGIC, EINVAL, handle)); continue
            sock.sendall(reply.pack(SIMPLE_REPLY_MAGIC, 0, handle) + exp.mem[off:off + length])
            exp.stats['reads'] += 1; exp.stats['bytes_out'] += length
        elif ctype == CMD_WRITE:
            data = recv_exact(sock, length)        # always drain the payload, even on error
            if bad:
                sock.sendall(reply.pack(SIMPLE_REPLY_MAGIC, ENOSPC, handle)); continue
            exp.mem[off:off + length] = data
            exp.stats['writes'] += 1; exp.stats['bytes_in'] += length
            sock.sendall(reply.pack(SIMPLE_REPLY_MAGIC, 0, handle))
        elif ctype == CMD_FLUSH:                    # RAM: nothing to flush
            sock.sendall(reply.pack(SIMPLE_REPLY_MAGIC, 0, handle))
        elif ctype == CMD_TRIM:
            if not bad: exp.trim(off, length); exp.stats['trims'] += 1
            sock.sendall(reply.pack(SIMPLE_REPLY_MAGIC, EINVAL if bad else 0, handle))
        elif ctype == CMD_DISC:
            return
        else:
            sock.sendall(reply.pack(SIMPLE_REPLY_MAGIC, EINVAL, handle))


def mem_available_mb():
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'): return int(line.split()[1]) // 1024
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description='Serve phone RAM as an NBD export for GhostRAM Link.')
    ap.add_argument('--size', default='1G', help='export size, e.g. 512M, 2G (default 1G)')
    ap.add_argument('--bind', default='127.0.0.1', help='address to listen on (default 127.0.0.1 for adb forward)')
    ap.add_argument('--port', type=int, default=10809)
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()
    size = parse_size(args.size)
    log = (lambda *a: None) if args.quiet else (lambda *a: print(time.strftime('%H:%M:%S'), *a, flush=True))

    avail = mem_available_mb()
    if avail is not None and size // (1 << 20) > avail * 0.8:
        log(f'warning: export {size >> 20} MB but only {avail} MB available on this device — Android may kill this app')
    exp = Export(size)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.port)); srv.listen(1)
    log(f'GhostRAM Link: serving {size >> 20} MB of RAM as NBD export "{EXPORT}" on {args.bind}:{args.port}')
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    # One client at a time: a block device must never be written by two hosts.
    while True:
        conn, peer = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        log(f'client {peer[0]} connected')
        try:
            if handshake(conn, exp, log): serve(conn, exp, log)
        except (ConnectionError, OSError) as e:
            log(f'client gone: {e}')
        finally:
            conn.close()
            s = exp.stats
            log(f"disconnected — reads {s['reads']}, writes {s['writes']}, trims {s['trims']}, "
                f"in {s['bytes_in'] >> 20} MB, out {s['bytes_out'] >> 20} MB")


if __name__ == '__main__':
    main()

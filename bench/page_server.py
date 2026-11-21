#!/usr/bin/env python3
"""
GhostRAM Link benchmark — phone side. Answers "give me page N" requests with 4 KB of data held in RAM.
Run it in Termux on the phone (no root), then run page_client.py on the PC.

    python3 page_server.py                  # 127.0.0.1:10810, for `adb forward tcp:10810 tcp:10810`
    python3 page_server.py --bind 0.0.0.0   # for USB tethering / Wi-Fi measurements

Request: 12 bytes = op (u32: 0 read, 1 write) + page number (u64). Write requests carry 4096 bytes after.
Reply:   read → 4096 bytes; write → 1 byte ack.
"""
import argparse, mmap, socket, struct, threading

PAGE = 4096
REQ = struct.Struct('>IQ')


def recv_exact(sock, n):
    buf = bytearray(n); view = memoryview(buf); got = 0
    while got < n:
        k = sock.recv_into(view[got:], n - got)
        if not k: raise ConnectionError
        got += k
    return buf


def handle(conn, mem, pages):
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        while True:
            op, page = REQ.unpack(recv_exact(conn, REQ.size))
            off = (page % pages) * PAGE
            if op == 0:
                conn.sendall(mem[off:off + PAGE])
            else:
                mem[off:off + PAGE] = recv_exact(conn, PAGE)
                conn.sendall(b'\x01')
    except (ConnectionError, OSError):
        pass
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bind', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=10810)
    ap.add_argument('--mb', type=int, default=256, help='RAM to hold (MB)')
    a = ap.parse_args()
    pages = a.mb * (1 << 20) // PAGE
    mem = mmap.mmap(-1, pages * PAGE)
    for i in range(0, pages * PAGE, PAGE): mem[i:i + 8] = struct.pack('>Q', i // PAGE)   # touch: really resident
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((a.bind, a.port)); srv.listen(16)
    print(f'page server: {a.mb} MB resident, listening on {a.bind}:{a.port}', flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn, mem, pages), daemon=True).start()   # one thread per stream (queue-depth tests)


if __name__ == '__main__':
    main()

"""Pre-warmed Python zygote worker (runs as a separate, single-threaded process).

Lifecycle:
  1. Started once by the server via subprocess. It pre-imports ctypes and
     dlopen()s the seccomp library ONCE, then never applies seccomp itself.
  2. It runs a single-threaded select() loop over the control socket and the
     stdout/stderr pipes of every live child.
  3. For each MSG_RUN it fork()s a child. The child inherits the already-warm
     interpreter + loaded .so via copy-on-write, then applies the sandbox
     (chroot + drop privileges + seccomp) and execs the user code. Cold-start
     of a fresh interpreter is avoided entirely.

IMPORTANT: this module is stdlib-only on purpose. It must NOT import the `app`
package, or the zygote would become a "fat" process again.

Invoked as:  python -B zygote_worker.py <ctrl_fd> <lib_so_path> <lib_dir> [warm_modules]
"""
import base64
import ctypes
import json
import os
import select
import signal
import socket
import sys
import traceback
from typing import TypedDict

# Make the sibling protocol module importable without pulling in `app`.
_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SELF_DIR)
import protocol as P  # noqa: E402

class _ReqInfo(TypedDict):
    """Bookkeeping for one in-flight sandbox request."""
    pid: int
    out: int
    err: int
    open: set[int]


# Max bytes buffered for the control socket before we stop draining child
# pipes (applying backpressure to the producing child instead of growing this
# buffer without bound).
_SEND_BUF_CAP = 4 * 1024 * 1024  # 4 MiB


def _log(msg: str) -> None:
    """Diagnostics go to the zygote's own stderr (captured by the server),
    never to fd 1/2 that children reuse."""
    sys.stderr.write(f"INFO sandbox_server::zygote: {msg}\n")
    sys.stderr.flush()


def _xor(data: bytes, key: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return b""
    kl = len(key)
    # XOR in key-aligned chunks. The big-int XOR runs in C (far faster than a
    # per-byte Python loop), but XORing the WHOLE buffer at once allocates
    # several n-sized temporaries simultaneously (~4x input at peak). Chunking
    # caps peak memory at ~O(chunk) while staying just as fast; each chunk is a
    # multiple of the key length so its keystream always starts at key[0].
    chunk = max(kl, (65536 // kl) * kl)  # ~64 KiB, aligned to key length
    out = bytearray(n)
    mv = memoryview(data)
    i = 0
    while i < n:
        end = i + chunk
        if end > n:
            end = n
        seg_len = end - i
        keystream = key * (seg_len // kl) + key[: seg_len % kl]
        out[i:end] = (
            int.from_bytes(mv[i:end], "big") ^ int.from_bytes(keystream, "big")
        ).to_bytes(seg_len, "big")
        i = end
    return bytes(out)


class Zygote:
    def __init__(self, ctrl_fd: int, lib_so: str, lib_dir: str, warm_modules=()):
        self.lib_dir = lib_dir
        # Children start (and later chroot) here; also where the .so lives.
        os.chdir(lib_dir)

        # Warm load of the seccomp library. Inherited by every child via COW.
        self.lib = ctypes.CDLL(lib_so)
        self.lib.init_seccomp.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint64, ctypes.c_bool]
        self.lib.init_seccomp.restype = ctypes.c_int

        self.lib.apply_landlock.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
        self.lib.apply_landlock.restype = ctypes.c_int

        # Initialize the tokenizer/codec machinery now (unrestricted parent) so
        # children can compile non-ASCII source after seccomp without triggering
        # a lazily-loaded codec/import that the sandbox would block.
        self._warm_codecs()

        # Pre-import common modules so forked children inherit them (COW) and
        # skip the filesystem lookup + compile on every `import`.
        self._warm_import(warm_modules)

        self.ctrl = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=ctrl_fd)
        # Non-blocking control socket: a slow manager must never block the
        # single-threaded event loop, which would stall EVERY child's output.
        self.ctrl.setblocking(False)
        self._recv_buf = bytearray()
        # Pending outbound bytes, flushed when the socket becomes writable.
        self._out = bytearray()

        # req_id -> {"pid", "out", "err", "open": set(fds)}
        self.reqs: dict[int, _ReqInfo] = {}
        # pipe read fd -> (req_id, msg_type)
        self.fd_map: dict[int, tuple[int, int]] = {}

        # Keep child zombies until we explicitly waitpid them.
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)

    @staticmethod
    def _warm_codecs() -> None:
        # Force the source-bytes tokenizer + codec registry to fully initialize
        # while still unrestricted, so the warmed caches are inherited by every
        # forked child. Without this, the first non-ASCII compile() inside a
        # sandboxed child can trigger a blocked syscall/import and surface as
        # "SyntaxError: UTF-8 decode error".
        try:
            import codecs
            import encodings          # noqa: F401
            import encodings.utf_8    # noqa: F401
            import encodings.aliases  # noqa: F401
            for enc in ("utf-8", "ascii", "latin-1", "utf-16", "idna"):
                try:
                    codecs.lookup(enc)
                except LookupError:
                    pass
            # Exercise the non-ASCII bytes tokenizer path and coding-cookie path.
            compile(b"# -*- coding: utf-8 -*-\n# \xe4\xbd\xa0\xe5\xa5\xbd\nx = '\xc3\xa9'\n",
                    "<warmup>", "exec")
            compile("# 你好\nx = 'é'\n", "<warmup>", "exec")
            b"\xc3\xa9".decode("utf-8")
            "é".encode("utf-8")
        except Exception as e:  # noqa: BLE001 - best effort
            _log(f"codec warmup issue: {e}")

    @staticmethod
    def _warm_import(modules: list[str]) -> None:
        import importlib
        ok = []
        for name in modules:
            name = name.strip()
            if not name:
                continue
            try:
                importlib.import_module(name)
                ok.append(name)
            except Exception as e:  # noqa: BLE001 - best effort, skip failures
                _log(f"warm import failed for {name!r}: {e}")
        if ok:
            _log(f"warm-imported {len(ok)} modules")

    # ------------------------------------------------------------------ IO
    def _send(self, msg_type: int, req_id: int, payload: bytes = b"") -> None:
        # Queue the frame and try a best-effort non-blocking flush. Never block:
        # a blocking sendall here freezes the whole event loop (and thus every
        # child's output) whenever the manager reads slowly.
        self._out += P.encode_frame(msg_type, req_id, payload)
        self._flush()

    def _flush(self) -> None:
        """Send as much buffered output as the socket will accept right now."""
        while self._out:
            try:
                sent = self.ctrl.send(self._out)
            except BlockingIOError:
                break  # kernel send buffer full; retry when writable
            except (BrokenPipeError, OSError):
                # Server went away; nothing left to do.
                os._exit(0)
            if sent <= 0:
                break
            del self._out[:sent]

    def _read_ctrl_frames(self):
        try:
            chunk = self.ctrl.recv(65536)
        except BlockingIOError:
            return  # spurious readiness; nothing to read yet
        if not chunk:
            # Control socket closed -> server gone.
            os._exit(0)
        self._recv_buf.extend(chunk)
        while len(self._recv_buf) >= P.HEADER_SIZE:
            plen, mtype, req_id = P.HEADER.unpack_from(self._recv_buf, 0)
            if len(self._recv_buf) < P.HEADER_SIZE + plen:
                break
            payload = bytes(self._recv_buf[P.HEADER_SIZE:P.HEADER_SIZE + plen])
            del self._recv_buf[:P.HEADER_SIZE + plen]
            yield mtype, req_id, payload

    # -------------------------------------------------------------- request
    def _handle_run(self, req_id: int, payload: bytes) -> None:
        req = json.loads(payload)
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()

        # Flush our own buffers so children never inherit unflushed bytes.
        sys.stdout.flush()
        sys.stderr.flush()

        pid = os.fork()
        if pid == 0:
            # ---- child ----
            self._child_exec(req, out_w, err_w, out_r, err_r)
            os._exit(127)  # unreachable

        # ---- parent (zygote) ----
        os.close(out_w)
        os.close(err_w)
        self.reqs[req_id] = {"pid": pid, "out": out_r, "err": err_r, "open": {out_r, err_r}}
        self.fd_map[out_r] = (req_id, P.MSG_STDOUT)
        self.fd_map[err_r] = (req_id, P.MSG_STDERR)

    def _child_exec(self, req: dict, out_w: int, err_w: int, out_r: int, err_r: int) -> None:
        # Redirect stdout/stderr to the pipes; disconnect from everything else.
        try:
            os.dup2(out_w, 1)
            os.dup2(err_w, 2)
            devnull = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull, 0)
            os.close(devnull)
            os.close(out_r)
            os.close(err_r)
            # Close EVERY inherited fd except stdio (0/1/2). Enumerating
            # /proc/self/fd catches descriptors an explicit close-set would
            # miss — the control socket, other requests' pipes, and especially
            # file/socket handles opened by warm-imported preload modules that
            # fork() duplicated into this child. Runs after the dup2 redirection
            # above, so 0/1/2 already point where they should.
            _keep = (0, 1, 2)
            try:
                _inherited = [int(e) for e in os.listdir("/proc/self/fd")]
            except (OSError, ValueError):
                _inherited = []
            if _inherited:
                for _fd in _inherited:
                    if _fd not in _keep:
                        try:
                            os.close(_fd)
                        except OSError:
                            pass
            else:
                # /proc unavailable: fall back to closing a bounded fd range.
                import resource
                _soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
                _maxfd = _soft if _soft not in (resource.RLIM_INFINITY, -1) else 65536
                os.closerange(3, _maxfd)

            # Drop the implicit ''/cwd entry (would call getcwd() post-seccomp)
            # and the zygote's own helper dir, so user code cannot re-import the
            # worker internals.
            sys.path = [p for p in sys.path if p and p != _SELF_DIR]
            sys.modules.pop("protocol", None)

            # Clear the control-socket receive buffer inherited from the parent
            # (COW). It may contain partial wire frames from other in-flight
            # requests. The child never reads from the control socket, but user
            # code could access it via gc.get_objects() introspection.
            self._recv_buf.clear()

            uid = int(req["uid"])
            gid = int(req["gid"])
            net = bool(req["net"])
            max_as = int(req.get("max_as", 0))
            privilege = bool(req.get("privilege", True))
            if not privilege:
                allowed = req.get("allowed_paths", [self.lib_dir])
                arr = (ctypes.c_char_p * (len(allowed) + 1))()
                for i, p in enumerate(allowed):
                    arr[i] = p.encode()
                arr[-1] = None
                ll_rc = self.lib.apply_landlock(arr)
                if ll_rc != 0:
                    raise RuntimeError(f"Landlock failed - {ll_rc}")
            rc = self.lib.init_seccomp(uid, gid, net, max_as, privilege)
            if rc != 0:
                raise RuntimeError(f"code executor err - {rc}")

            code = _xor(base64.b64decode(req["code"]), base64.b64decode(req["key"]))
            g = {"__name__": "__main__"}
            try:
                code_obj = compile(code, "<sandbox>", "exec")
            except SyntaxError:
                # Diagnostic: dump what compile actually received so we can tell
                # byte corruption apart from genuinely non-UTF-8 user source.
                import binascii
                valid_utf8 = True
                try:
                    code.decode("utf-8")
                except UnicodeDecodeError:
                    valid_utf8 = False
                sys.stderr.write(
                    f"[zygote-child] compile failed: "
                    f"code_len={len(code)} enc_len={len(req['code'])} "
                    f"key_len={len(base64.b64decode(req['key']))} "
                    f"valid_utf8={valid_utf8} "
                    f"head={binascii.hexlify(code[:24]).decode()} "
                    f"tail={binascii.hexlify(code[-24:]).decode()}\n"
                )
                sys.stderr.flush()
                raise
            exec(code_obj, g)

            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0)
        except SystemExit as e:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            code = e.code
            os._exit(code if isinstance(code, int) else 0)
        except BaseException:
            try:
                traceback.print_exc()
                sys.stderr.flush()
            except Exception:
                pass
            os._exit(1)

    def _handle_kill(self, req_id: int) -> None:
        req = self.reqs.get(req_id)
        if not req:
            return
        try:
            os.kill(req["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _on_pipe_readable(self, fd: int) -> None:
        req_id, mtype = self.fd_map[fd]
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        if data:
            self._send(mtype, req_id, data)
            return
        # EOF on this stream.
        os.close(fd)
        del self.fd_map[fd]
        req = self.reqs.get(req_id)
        if not req:
            return
        req["open"].discard(fd)
        if req["open"]:
            return
        # Both streams closed -> child exited; reap and report.
        try:
            _, status = os.waitpid(req["pid"], 0)
            exit_code = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            exit_code = -1
        self._send(P.MSG_DONE, req_id, P.DONE_STRUCT.pack(exit_code))
        del self.reqs[req_id]

    # ------------------------------------------------------------------ loop
    def serve(self) -> None:
        _log(f"ready (lib_dir={self.lib_dir})")
        while True:
            ctrl_fd = self.ctrl.fileno()
            # Only drain child pipes while the outbound buffer has headroom. If
            # the manager is slow and the buffer is full, stop reading children
            # (their pipes fill -> they block = backpressure on the producer),
            # but keep the loop alive to flush and to accept control frames.
            rlist = [ctrl_fd]
            if len(self._out) < _SEND_BUF_CAP:
                rlist += list(self.fd_map.keys())
            wlist = [ctrl_fd] if self._out else []
            try:
                readable, writable, _ = select.select(rlist, wlist, [])
            except InterruptedError:
                continue
            if writable:
                self._flush()
            for fd in readable:
                if fd == ctrl_fd:
                    for mtype, req_id, payload in self._read_ctrl_frames():
                        if mtype == P.MSG_RUN:
                            self._handle_run(req_id, payload)
                        elif mtype == P.MSG_KILL:
                            self._handle_kill(req_id)
                else:
                    if fd in self.fd_map:
                        self._on_pipe_readable(fd)


def main() -> None:
    ctrl_fd = int(sys.argv[1])
    lib_so = sys.argv[2]
    lib_dir = sys.argv[3]
    warm_modules = sys.argv[4].split(",") if len(sys.argv) > 4 and sys.argv[4] else []
    Zygote(ctrl_fd, lib_so, lib_dir, warm_modules).serve()


if __name__ == "__main__":
    main()

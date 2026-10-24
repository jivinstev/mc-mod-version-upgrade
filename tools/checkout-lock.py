#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One run per checkout (CATALOG X44): the lock tools/fork-port.py and `ci-gates.py --mc` (a version branch) take on
build/.fork-port.lock (pid, stale-pid aware), and the pid check tools/round.py uses.

    python3 tools/checkout-lock.py --self-check

A second run on the same checkout is refused at once with a message naming the holder; the holder's own children
(LOCK_ENV names its pid: fork-port.py -> ci-gates.py, layer-deps.py) are let through; a lock whose process is gone is
taken over. A single-version fork's gate run never takes it. Standard library only.
"""
import datetime, json, os, pathlib, sys, tempfile

LOCK_REL = "build/.fork-port.lock"
LOCK_ENV = "MV_CHECKOUT_LOCK_HOLDER"      # a child of the lock holder (fork-port.py -> ci-gates.py) is let through


def pid_alive(pid):
    """Is a process with this pid running? (A reused pid reads as alive: the lock then needs deleting by hand, which
    the refusal message says.)"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259                            # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class CheckoutLock:
    """An exclusive lock on one checkout: build/.fork-port.lock holding {"pid", "tool", "target", "started"}.
    acquire() -> None when taken (or inherited from a live parent that holds it), else the refusal message.
    A lock whose pid is dead is stale and is taken over. release() removes only a lock this object created."""

    def __init__(self, repo, tool, target="", pid=None, alive=pid_alive, env=None):
        self.path = pathlib.Path(repo) / LOCK_REL
        self.tool, self.target = tool, target
        self.pid = pid if pid is not None else os.getpid()
        self.alive = alive
        self.env = env if env is not None else os.environ
        self.mine = False

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            body = json.dumps({"pid": self.pid, "tool": self.tool, "target": self.target,
                               "started": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                held = self._read()
                hp = held.get("pid") if isinstance(held, dict) else None
                if isinstance(hp, int) and hp != self.pid and self.alive(hp):
                    if str(hp) == str(self.env.get(LOCK_ENV, "")):
                        return None           # our parent holds it and started us: its run, not a second one
                    return (f"another {held.get('tool', 'run')} is using this checkout (pid {hp}, target "
                            f"{held.get('target') or '-'}, since {held.get('started', '?')}): one gate run per checkout "
                            f"(CATALOG X44) -- two share build/, the Gradle daemon and the CPU, and their results are "
                            f"not attributable. Wait for it, or if pid {hp} is not really that run, delete {self.path}")
                try:
                    self.path.unlink()           # stale (dead pid) or unreadable: take it over
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            self.mine = True
            self.env[LOCK_ENV] = str(self.pid)
            return None
        return f"could not take {self.path} (it keeps being recreated by another process)"

    def release(self):
        if self.mine:
            held = self._read()
            if isinstance(held, dict) and held.get("pid") == self.pid:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
            if self.env.get(LOCK_ENV) == str(self.pid):
                del self.env[LOCK_ENV]
            self.mine = False


def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    with tempfile.TemporaryDirectory() as d:
        alive = {1001, 1002}
        mk = lambda pid, env=None, tool="fork-port.py": CheckoutLock(d, tool, "1.21.4", pid=pid, alive=lambda x: x in alive,
                                                                       env={} if env is None else env)
        a_, b_ = mk(1001), mk(1002)
        expect(a_.acquire() is None and (pathlib.Path(d) / LOCK_REL).is_file(), "first run takes the lock")
        msg = b_.acquire()
        expect(msg and "1001" in msg and "one gate run per checkout" in msg and "delete" in msg, f"second run refused: {msg}")
        expect(not b_.mine, "a refused run owns nothing")
        b_.release()
        expect((pathlib.Path(d) / LOCK_REL).is_file(), "a refused run's release leaves the holder's lock")
        kid = mk(1002, env={LOCK_ENV: "1001"}, tool="ci-gates.py")
        expect(kid.acquire() is None and not kid.mine, "a child of the holder is let through and owns nothing")
        kid.release()
        expect((pathlib.Path(d) / LOCK_REL).is_file(), "the child's release leaves the parent's lock")
        a_.release()
        expect(not (pathlib.Path(d) / LOCK_REL).exists(), "release removes the lock")
        (pathlib.Path(d) / LOCK_REL).write_text(json.dumps({"pid": 4242, "tool": "fork-port.py"}), encoding="utf-8")
        c_ = mk(1002)
        expect(c_.acquire() is None and c_.mine and json.loads((pathlib.Path(d) / LOCK_REL).read_text(encoding="utf-8"))["pid"] == 1002,
               "a dead holder's lock is stale and taken over")
        c_.release()
        (pathlib.Path(d) / LOCK_REL).write_text("garbage", encoding="utf-8")
        e_ = mk(1001)
        expect(e_.acquire() is None, "an unreadable lock file is stale")
        e_.release()
    expect(pid_alive(os.getpid()) and not pid_alive(0), "pid_alive: this process is alive, pid 0 is not")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(self_check() if "--self-check" in sys.argv else (print(__doc__.split("\n")[2].strip()) or 2))

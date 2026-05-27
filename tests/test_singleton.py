"""Singleton lock tests."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

from vpnpilot._singleton import SingletonLock


def test_first_acquire_succeeds(tmp_path):
    lock = SingletonLock(dir=tmp_path)
    assert lock.acquire() is True


def test_second_acquire_in_same_process_fails(tmp_path):
    a = SingletonLock(dir=tmp_path)
    b = SingletonLock(dir=tmp_path)
    assert a.acquire() is True
    assert b.acquire() is False


def test_lock_records_pid(tmp_path):
    lock = SingletonLock(dir=tmp_path)
    assert lock.acquire() is True
    held = SingletonLock(dir=tmp_path).held_by_pid()
    assert held == os.getpid()


def test_lock_releases_when_holder_exits(tmp_path):
    """A subprocess holds the lock, exits, then a fresh acquire succeeds.

    This is the failure mode we actually care about: a previous vpnpilot
    crashed and we need to be able to start back up.
    """
    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
    program = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {src_dir!r})
        from vpnpilot._singleton import SingletonLock
        ok = SingletonLock(dir={str(tmp_path)!r}).acquire()
        print('OK' if ok else 'FAIL')
        # subprocess exits here, releasing the fd
        """
    )
    cp = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=10)
    assert cp.stdout.strip() == "OK", cp.stderr

    # Now a new lock object in this process should be able to acquire it.
    lock = SingletonLock(dir=tmp_path)
    assert lock.acquire() is True


def test_symlink_lock_path_is_refused(tmp_path):
    target = tmp_path / "target"
    target.write_text("do not truncate", encoding="utf-8")
    (tmp_path / "vpnpilot.lock").symlink_to(target)

    lock = SingletonLock(dir=tmp_path)

    assert lock.acquire() is False
    assert target.read_text(encoding="utf-8") == "do not truncate"


def test_wrong_owner_lock_file_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "vpnpilot.lock"
    path.write_text("", encoding="utf-8")
    real_fstat = os.fstat

    def fake_fstat(fd):
        st = real_fstat(fd)
        if os.path.samefile(f"/proc/self/fd/{fd}", path):
            return SimpleNamespace(st_mode=st.st_mode, st_uid=os.getuid() + 1)
        return st

    monkeypatch.setattr(os, "fstat", fake_fstat)

    assert SingletonLock(dir=tmp_path).acquire() is False


def test_open_eacces_is_not_acquired(tmp_path, monkeypatch):
    def fake_open(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "open", fake_open)

    assert SingletonLock(dir=tmp_path).acquire() is False

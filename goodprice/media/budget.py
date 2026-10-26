"""Single-process disk accounting, shared by all media workers for a root.

Pending directories are reservations, including every intermediate output.
Only one application process may own a data directory (also true of scheduler).
"""
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class _State:
    lock: threading.RLock = field(default_factory=threading.RLock)
    reservations: dict = field(default_factory=dict)
    pins: dict = field(default_factory=dict)


_states = {}
_states_lock = threading.Lock()


@dataclass
class BudgetReservation:
    token: str
    bytes_reserved: int
    directory: Path
    budget: "MediaBudget"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.budget.release(self.token)


class MediaBudget:
    def __init__(self, root: Path, max_bytes: int, retention_seconds: int,
                 safety_bytes: int = 64 * 1024**2):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.retention_seconds = retention_seconds
        self.safety_bytes = safety_bytes
        with _states_lock:
            if self.root not in _states:
                # Crash leftovers are never served. No external path is followed.
                for path in self.root.glob("pending-*"):
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                _states[self.root] = _State()
            self.state = _states[self.root]

    def checked(self, path: Path) -> Path:
        absolute = path.resolve()
        if not absolute.is_relative_to(self.root) or path.is_symlink():
            raise ValueError("Media path outside controlled cache")
        return absolute

    def _files(self):
        return [p for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()
                and p.resolve().is_relative_to(self.root)
                and not any(part.startswith("pending-") for part in p.relative_to(self.root).parts)]

    @property
    def usage_bytes(self):
        with self.state.lock:
            return sum(p.stat().st_size for p in self._files()) + sum(
                reservation.bytes_reserved for reservation in self.state.reservations.values())

    def reserve(self, key: str, bytes_needed: int) -> BudgetReservation:
        if bytes_needed < 1 or bytes_needed > self.max_bytes:
            raise BudgetExceeded("单项媒体预算超过缓存容量，请降低文件或采样上限")
        with self.state.lock:
            files = sorted(self._files(), key=lambda p: p.stat().st_mtime)
            for path in files:
                if self.state.pins.get(path.resolve(), 0):
                    continue
                if (time.time() - path.stat().st_mtime >= self.retention_seconds
                        or self.usage_bytes + bytes_needed > self.max_bytes):
                    path.unlink(missing_ok=True)
            if (self.usage_bytes + bytes_needed > self.max_bytes or
                    shutil.disk_usage(self.root).free < bytes_needed + self.safety_bytes):
                raise BudgetExceeded("媒体磁盘预算暂不可用，请稍后重试或降低预算")
            token = uuid.uuid4().hex
            directory = self.root / f"pending-{token}"
            directory.mkdir()
            reservation = BudgetReservation(token, bytes_needed, directory, self)
            self.state.reservations[token] = reservation
            return reservation

    def commit(self, token: str, paths: list[Path]) -> None:
        with self.state.lock:
            reservation = self.state.reservations[token]
            paths = [self.checked(p) for p in paths]
            if any(not p.is_relative_to(reservation.directory) for p in paths):
                raise ValueError("Output does not belong to this reservation")
            # Check ALL files, not only outputs supplied by the caller.
            size = sum(p.stat().st_size for p in reservation.directory.rglob("*") if p.is_file())
            if size > reservation.bytes_reserved:
                raise BudgetExceeded("媒体输出超过预留空间")
            # Keep output paths stable; remove the pending prefix via move then
            # return paths through committed_paths. No file can escape its root.
            destination = self.root / f"asset-{token}"
            relative = [p.relative_to(reservation.directory) for p in paths]
            kept = set(paths)
            for p in reservation.directory.rglob("*"):
                if p.is_file() and p.resolve() not in kept:
                    p.unlink()
            reservation.directory.rename(destination)
            reservation.committed_paths = [destination / p for p in relative]
            del self.state.reservations[token]

    def release(self, token: str) -> None:
        with self.state.lock:
            reservation = self.state.reservations.pop(token, None)
            if reservation and reservation.directory.exists():
                self.checked(reservation.directory)
                shutil.rmtree(reservation.directory)

    @contextmanager
    def pin(self, paths):
        with self.state.lock:
            checked = [self.checked(p) for p in paths]
            for p in checked:
                self.state.pins[p] = self.state.pins.get(p, 0) + 1
        try:
            yield
        finally:
            with self.state.lock:
                for p in checked:
                    self.state.pins[p] -= 1

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceLimits:
    active_runs: int = 2
    crawler_workers: int = 1
    model_workers: int = 1
    media_workers: int = 1
    platform_interval_seconds: int = 10
    model_rpm: int = 10
    global_items: int = 200
    task_items: int = 60
    run_details: int = 30
    run_http_attempts: int = 60
    run_minutes: int = 15

    def __post_init__(self):
        bounds = {"active_runs": (1, 4), "crawler_workers": (1, 2), "model_workers": (1, 8),
            "media_workers": (1, 2), "platform_interval_seconds": (5, 300), "model_rpm": (1, 60),
            "global_items": (1, 200), "task_items": (1, 60), "run_details": (1, 100),
            "run_http_attempts": (1, 200), "run_minutes": (5, 60)}
        for field, (minimum, maximum) in bounds.items():
            if not minimum <= getattr(self, field) <= maximum:
                raise ValueError(f"{field} must be {minimum}..{maximum}")

    @classmethod
    def from_settings(cls, settings):
        return cls(**{name: getattr(settings, f"execution_{name}") for name in cls.__dataclass_fields__})


class RateGate:
    def __init__(self, interval_seconds):
        self.interval = interval_seconds
        self._next = {}
        self._lock = threading.Lock()

    def ready_at(self, key, now):
        with self._lock:
            return max(now, self._next.get(key, now))

    def reserve(self, key, now):
        with self._lock:
            allowed = max(now, self._next.get(key, now))
            self._next[key] = allowed + self.interval
            return allowed

    def postpone(self, key, until):
        with self._lock:
            self._next[key] = max(until, self._next.get(key, until))

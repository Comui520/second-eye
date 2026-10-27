"""Read-only resource protection; saved user settings are never rewritten."""
import time
from dataclasses import dataclass, replace

from goodprice.execution.limits import ResourceLimits
from goodprice.resources import detect_resources


@dataclass(frozen=True)
class EffectiveResources:
    limits: ResourceLimits
    paused: frozenset[str]
    reasons: tuple[str, ...]
    low: bool


class ResourceGuard:
    def __init__(self, detector=None, clock=time.monotonic):
        self.detector, self.clock = detector or detect_resources, clock
        self._at, self._snapshot = 0, None

    def evaluate(self, configured):
        if self._snapshot is None or self.clock() - self._at >= 5:
            self._snapshot, self._at = self.detector(), self.clock()
        snapshot = self._snapshot
        available = snapshot.available_memory_bytes
        low = snapshot.source == "unknown" or available is None or available < 1024**3
        paused, reasons = set(), []
        limits = configured
        if low:
            limits = replace(configured, crawler_workers=1, model_workers=1, media_workers=1)
            reasons.append("内存余量较小或检测不完整，实际工作并发暂限为1")
        if available is not None:
            for lane, reserve_mib in {"crawler": 256, "media": 192, "model": 128}.items():
                if available < reserve_mib * 1024**2:
                    paused.add(lane)
            if paused:
                reasons.append("可用内存不足，暂缓新请求并保留待执行步骤")
        return EffectiveResources(limits, frozenset(paused), tuple(reasons), low)

    def media_settings(self, settings):
        from goodprice.media.budget import BudgetExceeded
        current = self.evaluate(ResourceLimits.from_settings(settings))
        if "media" in current.paused:
            raise BudgetExceeded("媒体处理等待内存余量")
        return settings.model_copy(update={"execution_media_workers": current.limits.media_workers,
            "media_ffmpeg_threads": 1 if current.low else settings.media_ffmpeg_threads})

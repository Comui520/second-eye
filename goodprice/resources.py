"""Local, read-only capability detection. Recommendations never apply themselves."""
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class ResourceSnapshot:
    effective_cpu: float | None
    memory_limit_bytes: int | None
    available_memory_bytes: int | None
    source: str
    warnings: tuple[str, ...]
    detected_at: datetime


def resource_profile(name):
    if name not in {"low", "balanced"}:
        raise ValueError("Unknown resource profile")
    return {"execution_active_runs": 2, "execution_crawler_workers": 1,
            "execution_model_workers": 2 if name == "balanced" else 1,
            "execution_media_workers": 1, "media_ffmpeg_threads": 1}


def recommend_profile(snapshot):
    balanced = (snapshot.source != "unknown" and (snapshot.effective_cpu or 0) >= 4
        and (snapshot.memory_limit_bytes or 0) >= 6 * 1024**3
        and (snapshot.available_memory_bytes or 0) >= 3 * 1024**3)
    name = "balanced" if balanced else "low"
    return {"name": name, "values": resource_profile(name),
            "reason": "有效CPU及内存余量足够，可选择均衡" if balanced else "设备余量较小或检测不完整，建议低占用"}


def _cpus(raw):
    values = set()
    for part in raw.strip().split(","):
        if not part:
            continue
        bounds = part.split("-")
        start, end = int(bounds[0]), int(bounds[-1])
        if start < 0 or end < start or end - start > 65536:
            raise ValueError()
        values.update(range(start, end + 1))
    return values


def _unescape(raw):
    for escaped, value in [("\\040", " "), ("\\011", "\t"), ("\\134", "\\")]:
        raw = raw.replace(escaped, value)
    return raw


def detect_resources(*, system=None, read=None, host=None, affinity=None):
    system = system or platform.system()
    read = read or (lambda p: Path(p).read_text(encoding="utf-8"))
    warnings = []
    if host is None:
        try:
            import psutil
            memory = psutil.virtual_memory()
            host = (psutil.cpu_count(logical=True), memory.total, memory.available)
            try:
                affinity = set(psutil.Process().cpu_affinity())
            except (AttributeError, NotImplementedError, psutil.Error):
                pass
        except (ImportError, OSError):
            host = (None, None, None)
            warnings.append("系统资源检测不可用")
    cpu, memory_limit, available = host
    if affinity:
        cpu = min(cpu, len(affinity)) if cpu else len(affinity)
    source = "host" if all(v is not None for v in host) else "unknown"
    def optional(path):
        try:
            return read(str(path)).strip()
        except (OSError, KeyError, ValueError):
            return ""
    if system == "Linux":
        groups = optional("/proc/self/cgroup")
        mounts = optional("/proc/self/mountinfo")
        if not groups or not mounts:
            source = "unknown"
            warnings.append("无法读取容器层级，保守推荐")
        else:
            mappings = []
            for line in groups.splitlines():
                parts = line.split(":", 2)
                if len(parts) == 3:
                    mappings.append((set(parts[1].split(",")), PurePosixPath(parts[2])))
            for line in mounts.splitlines():
                fields = line.split()
                try:
                    sep = fields.index("-")
                    fs = fields[sep+1]
                    if fs not in {"cgroup", "cgroup2"}:
                        continue
                    mount_root, mount = PurePosixPath(_unescape(fields[3])), PurePosixPath(_unescape(fields[4]))
                    controllers = set(fields[sep+3].split(","))
                    for names, process in mappings:
                        if fs == "cgroup" and not (controllers & names):
                            continue
                        if fs == "cgroup2" and names != {""}:
                            continue
                        if not process.is_relative_to(mount_root):
                            continue
                        directory = mount / process.relative_to(mount_root)
                        for ancestor in (directory, *directory.parents):
                            if not ancestor.is_relative_to(mount):
                                break
                            quota = optional(ancestor / "cpu.max") if fs == "cgroup2" else " ".join([
                                optional(ancestor / "cpu.cfs_quota_us"), optional(ancestor / "cpu.cfs_period_us")])
                            parts = quota.split()
                            if len(parts) == 2 and parts[0] not in {"max", "-1"}:
                                limit = int(parts[0]) / int(parts[1])
                                if limit > 0:
                                    cpu = min(cpu, limit) if cpu else limit
                                    source = "cgroup"
                            cpuset = optional(ancestor / ("cpuset.cpus.effective" if fs == "cgroup2" else "cpuset.cpus"))
                            if cpuset:
                                ids = _cpus(cpuset)
                                if affinity:
                                    ids &= affinity
                                if ids:
                                    cpu = min(cpu, len(ids)) if cpu else len(ids)
                                    source = "cgroup"
                            raw_limit = optional(ancestor / ("memory.max" if fs == "cgroup2" else "memory.limit_in_bytes"))
                            raw_current = optional(ancestor / ("memory.current" if fs == "cgroup2" else "memory.usage_in_bytes"))
                            if raw_limit and raw_limit != "max":
                                limit = int(raw_limit)
                                if 0 < limit < 2**60:
                                    memory_limit = min(memory_limit, limit) if memory_limit else limit
                                    source = "cgroup"
                                    if raw_current:
                                        remaining = max(0, limit - int(raw_current))
                                        available = min(available, remaining) if available is not None else remaining
                                    else:
                                        available = 0
                                        warnings.append("容器内存占用不可读")
                except (ValueError, IndexError, ZeroDivisionError):
                    source = "unknown"
                    warnings.append("容器资源格式无法解析")
    return ResourceSnapshot(float(cpu) if cpu is not None else None, memory_limit, available,
                            source, tuple(dict.fromkeys(warnings)), datetime.now(timezone.utc))

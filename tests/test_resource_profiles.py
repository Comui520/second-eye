from datetime import datetime, timezone

from goodprice.resources import (
    ResourceSnapshot,
    detect_resources,
    recommend_profile,
    resource_profile,
)

GIB = 1024**3


def snapshot(cpu=4, memory=6*GIB, available=3*GIB, source="host"):
    return ResourceSnapshot(cpu, memory, available, source, (), datetime.now(timezone.utc))


def test_recommendation_never_raises_media_or_rate_budgets():
    assert recommend_profile(snapshot())["name"] == "balanced"
    for info in [snapshot(cpu=3.9), snapshot(memory=5*GIB), snapshot(available=2*GIB), snapshot(source="unknown")]:
        assert recommend_profile(info)["name"] == "low"
    assert list(resource_profile("low").values()) == [2, 1, 1, 1, 1]
    assert list(resource_profile("balanced").values()) == [2, 1, 2, 1, 1]
    assert not any("quota" in k or "rpm" in k or "total_limit" in k for k in resource_profile("balanced"))


def test_cgroup_v2_nested_limits_and_sparse_cpuset():
    files = {
        "/proc/self/cgroup": "0::/docker/parent/child\n",
        "/proc/self/mountinfo": "1 2 0:1 /docker /sys/fs/cgroup rw - cgroup2 cgroup rw\n",
        "/sys/fs/cgroup/parent/child/cpu.max": "400000 100000",
        "/sys/fs/cgroup/parent/cpu.max": "150000 100000",
        "/sys/fs/cgroup/parent/child/cpuset.cpus.effective": "0-1,4,7",
        "/sys/fs/cgroup/parent/child/memory.max": str(8*GIB),
        "/sys/fs/cgroup/parent/child/memory.current": str(GIB),
        "/sys/fs/cgroup/parent/memory.max": str(4*GIB),
        "/sys/fs/cgroup/parent/memory.current": str(3*GIB),
    }
    result = detect_resources(system="Linux", read=lambda p: files[p], host=(16, 32*GIB, 20*GIB), affinity={0,1,4,7})
    assert result.effective_cpu == 1.5
    assert result.memory_limit_bytes == 4*GIB
    assert result.available_memory_bytes == GIB
    assert result.source == "cgroup"


def test_cgroup_v1_and_host_availability_minimum():
    files = {
        "/proc/self/cgroup": "2:cpu,cpuacct:/team/app\n3:memory:/team/app\n",
        "/proc/self/mountinfo": "1 2 0:1 / /sys/fs/cgroup/cpu rw - cgroup cgroup rw,cpu,cpuacct\n2 2 0:2 / /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory\n",
        "/sys/fs/cgroup/cpu/team/app/cpu.cfs_quota_us": "200000",
        "/sys/fs/cgroup/cpu/team/app/cpu.cfs_period_us": "100000",
        "/sys/fs/cgroup/memory/team/app/memory.limit_in_bytes": str(8*GIB),
        "/sys/fs/cgroup/memory/team/app/memory.usage_in_bytes": str(GIB),
    }
    result = detect_resources(system="Linux", read=lambda p: files[p], host=(8, 16*GIB, GIB), affinity=set(range(8)))
    assert result.effective_cpu == 2
    assert result.available_memory_bytes == GIB


def test_missing_cgroup_permissions_are_conservative_and_mac_windows_work():
    def denied(path): raise PermissionError()
    for system in ("Windows", "Darwin"):
        assert detect_resources(system=system, read=denied, host=(8, 16*GIB, 8*GIB), affinity=None).source == "host"
    unknown = detect_resources(system="Linux", read=denied, host=(8, 16*GIB, 8*GIB), affinity=None)
    assert recommend_profile(unknown)["name"] == "low"

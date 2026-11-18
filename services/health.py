# Copyright (c) 2025 Stephen G. Pope
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
How busy the toolkit's server is right now, for /v1/toolkit/health.

Inside a container the machine-wide numbers (/proc/stat, /proc/meminfo) describe the
host, not the toolkit, so CPU and memory are read from the container's cgroup first, the
same way `docker stats` does, and from the whole machine only when there is no cgroup.
"""

import json
import os
import shutil
import time

from services import job_queue
from services.cpu_limits import available_cpus

CGROUP_ROOT = "/sys/fs/cgroup"
# Jobs in the last hour are counted.
JOBS_WINDOW_SECONDS = 3600

_STARTED_AT = time.time()


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _cpu_usage_seconds(root=CGROUP_ROOT):
    """CPU time the container has used so far, in seconds, or None without a cgroup."""
    stat = _read(os.path.join(root, "cpu.stat"))
    if stat:
        for line in stat.splitlines():
            name, _, value = line.partition(" ")
            if name == "usage_usec":
                return int(value) / 1_000_000
    raw = _read(os.path.join(root, "cpuacct", "cpuacct.usage")) or _read(
        os.path.join(root, "cpuacct.usage")
    )
    if raw and raw.isdigit():
        return int(raw) / 1_000_000_000
    return None


def cpu_percent(sample_seconds=0.5, root=CGROUP_ROOT, cpus=None, sleep=time.sleep):
    """
    How much of the CPUs the toolkit may use it is using, 0 to 100, measured over a short
    moment.
    """
    cpus = cpus or available_cpus()
    start = _cpu_usage_seconds(root)
    if start is not None:
        begin = time.monotonic()
        sleep(sample_seconds)
        end = _cpu_usage_seconds(root)
        elapsed = max(time.monotonic() - begin, 1e-6)
        if end is not None:
            return round(min(100.0, max(0.0, (end - start) / elapsed / cpus * 100)), 1)
    import psutil

    return round(psutil.cpu_percent(interval=sample_seconds), 1)


def _memory_stat(root):
    stat = {}
    for line in (_read(os.path.join(root, "memory.stat")) or "").splitlines():
        name, _, value = line.partition(" ")
        if value.isdigit():
            stat[name] = int(value)
    return stat


def memory(root=CGROUP_ROOT):
    """
    {"used": bytes, "limit": bytes}. Used leaves out file cache the kernel can drop at
    once, like `docker stats`; the limit is the container's, or the machine's memory
    when the container has none.
    """
    import psutil

    machine = psutil.virtual_memory().total
    current = _read(os.path.join(root, "memory.current"))
    if current and current.isdigit():
        limit = _read(os.path.join(root, "memory.max"))
        cache = _memory_stat(root).get("inactive_file", 0)
        return {
            "used": max(0, int(current) - cache),
            "limit": int(limit) if limit and limit.isdigit() else machine,
        }
    usage = _read(os.path.join(root, "memory", "memory.usage_in_bytes"))
    if usage and usage.isdigit():
        limit = _read(os.path.join(root, "memory", "memory.limit_in_bytes"))
        cache = _memory_stat(os.path.join(root, "memory")).get("total_inactive_file", 0)
        limit_bytes = int(limit) if limit and limit.isdigit() else machine
        # cgroup v1 reports "no limit" as a huge number.
        return {"used": max(0, int(usage) - cache), "limit": min(limit_bytes, machine)}
    vm = psutil.virtual_memory()
    return {"used": vm.total - vm.available, "limit": vm.total}


def disk(path):
    """Space where the toolkit keeps files while it works on them."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"free": usage.free, "total": usage.total}


def _local_statuses(jobs_dir, since_seconds):
    cutoff = time.time() - since_seconds
    statuses = {}
    try:
        names = os.listdir(jobs_dir)
    except OSError:
        return statuses
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(jobs_dir, name)
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            with open(path) as f:
                statuses[name[:-5]] = json.load(f).get("job_status")
        except (OSError, ValueError):
            continue
    return statuses


def job_counts(local_storage_path, since_seconds=JOBS_WINDOW_SECONDS, leave_out=None):
    """
    How many jobs are queued, running, done in the last hour, from every team. The
    health request is a job too while it answers; `leave_out` is its id.
    """
    statuses = None
    if job_queue.enabled():
        try:
            statuses = job_queue.recent_statuses(since_seconds)
        except Exception:
            statuses = None
    if statuses is None:
        statuses = _local_statuses(os.path.join(local_storage_path, "jobs"), since_seconds)
    counts = {"queued": 0, "running": 0, "done": 0}
    for job_id, status in statuses.items():
        if job_id != leave_out and status in counts:
            counts[status] += 1
    return counts


def queue():
    """The shared Redis queue's workers and waiting jobs, or that each process has its own."""
    if not job_queue.enabled():
        return {"shared": False, "waiting": None, "workers": None, "busy": None}
    try:
        from rq import Worker

        workers = Worker.all(connection=job_queue.get_connection())
        return {
            "shared": True,
            "waiting": job_queue.queue_length(),
            "workers": len(workers),
            "busy": sum(1 for worker in workers if worker.get_state() == "busy"),
        }
    except Exception:
        return {"shared": True, "waiting": None, "workers": None, "busy": None, "reachable": False}


def snapshot(local_storage_path, sample_seconds=0.5, job_id=None):
    from version import BUILD_NUMBER

    cpus = available_cpus()
    return {
        "build_number": BUILD_NUMBER,
        "cpus": cpus,
        "cpu_percent": cpu_percent(sample_seconds, cpus=cpus),
        "memory": memory(),
        "disk": disk(local_storage_path),
        "uptime_seconds": round(time.time() - _STARTED_AT),
        "queue": queue(),
        "jobs": job_counts(local_storage_path, leave_out=job_id),
    }

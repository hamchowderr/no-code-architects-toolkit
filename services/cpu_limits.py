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
How many CPUs this process may really use.

os.cpu_count() reports the whole machine. In a container limited with `cpus: 2` (Docker,
Coolify, Kubernetes) the limit lives in the cgroup, and sizing workers or threads from the
machine's count overloads the container.
"""

import math
import os

CGROUP_V2 = "/sys/fs/cgroup/cpu.max"
CGROUP_V1_QUOTA = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
CGROUP_V1_PERIOD = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def cgroup_cpu_limit(v2_path=CGROUP_V2, v1_quota=CGROUP_V1_QUOTA, v1_period=CGROUP_V1_PERIOD):
    """CPUs allowed by the cgroup quota, rounded up, or None when there is no limit."""
    raw = _read(v2_path)
    if raw:
        parts = raw.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                return max(1, math.ceil(int(parts[0]) / int(parts[1])))
            except (ValueError, ZeroDivisionError):
                return None
        return None
    quota, period = _read(v1_quota), _read(v1_period)
    try:
        if quota and period and int(quota) > 0:
            return max(1, math.ceil(int(quota) / int(period)))
    except (ValueError, ZeroDivisionError):
        pass
    return None


def available_cpus():
    counts = [os.cpu_count() or 1]
    try:
        counts.append(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        pass
    limit = cgroup_cpu_limit()
    if limit:
        counts.append(limit)
    return max(1, min(counts))

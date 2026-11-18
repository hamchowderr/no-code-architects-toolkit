"""
/v1/toolkit/health: how busy the toolkit's server is. CPU and memory are read from fake
cgroup files here, so the numbers are known; the route itself runs for real.
"""

import json
import os

import fakeredis
import pytest

import app as app_module
from services import health, job_queue

from conftest import API_KEY


def write(root, name, text):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Clock:
    """Stands in for time.sleep, moving the container's CPU counter on as it 'waits'."""

    def __init__(self, root, used_per_call):
        self.root = root
        self.used = 0
        self.used_per_call = used_per_call

    def sleep(self, seconds):
        self.used += self.used_per_call
        write(self.root, "cpu.stat", f"usage_usec {self.used}\nuser_usec 1\nsystem_usec 1\n")


def test_cpu_is_the_containers_share_of_its_own_cpus(tmp_path, monkeypatch):
    root = str(tmp_path)
    write(root, "cpu.stat", "usage_usec 0\n")
    # 1 second of CPU time over a moment that took ~0 s of wall time would be huge, so the
    # wall clock is pinned too: 0.5 s passes, 1 CPU-second is used, on 4 CPUs = 50%.
    times = iter([100.0, 100.5])
    monkeypatch.setattr(health.time, "monotonic", lambda: next(times))
    clock = Clock(root, used_per_call=1_000_000)
    assert health.cpu_percent(0.5, root=root, cpus=4, sleep=clock.sleep) == 50.0


def test_cpu_never_reads_above_100(tmp_path, monkeypatch):
    root = str(tmp_path)
    write(root, "cpu.stat", "usage_usec 0\n")
    times = iter([0.0, 0.5])
    monkeypatch.setattr(health.time, "monotonic", lambda: next(times))
    clock = Clock(root, used_per_call=9_000_000)
    assert health.cpu_percent(0.5, root=root, cpus=2, sleep=clock.sleep) == 100.0


def test_cpu_from_cgroup_v1(tmp_path, monkeypatch):
    root = str(tmp_path)
    write(root, "cpuacct/cpuacct.usage", "0")
    times = iter([0.0, 1.0])
    monkeypatch.setattr(health.time, "monotonic", lambda: next(times))

    def sleep(_):
        write(root, "cpuacct/cpuacct.usage", str(500_000_000))

    assert health.cpu_percent(1.0, root=root, cpus=1, sleep=sleep) == 50.0


def test_memory_leaves_out_droppable_file_cache_like_docker_stats(tmp_path):
    root = str(tmp_path)
    write(root, "memory.current", str(3 * 2**30))
    write(root, "memory.max", str(16 * 2**30))
    write(root, "memory.stat", f"anon 100\ninactive_file {2**30}\nactive_file 5\n")
    assert health.memory(root) == {"used": 2 * 2**30, "limit": 16 * 2**30}


def test_memory_without_a_limit_is_out_of_the_machines_memory(tmp_path):
    import psutil

    root = str(tmp_path)
    write(root, "memory.current", "1000")
    write(root, "memory.max", "max")
    assert health.memory(root) == {"used": 1000, "limit": psutil.virtual_memory().total}


def test_memory_from_cgroup_v1_ignores_its_no_limit_number(tmp_path):
    import psutil

    root = str(tmp_path)
    write(root, "memory/memory.usage_in_bytes", "5000")
    write(root, "memory/memory.limit_in_bytes", str(2**63 - 4096))
    write(root, "memory/memory.stat", "total_inactive_file 1000\n")
    assert health.memory(root) == {"used": 4000, "limit": psutil.virtual_memory().total}


def test_memory_with_no_cgroup_uses_the_machine(tmp_path):
    result = health.memory(str(tmp_path))
    assert 0 < result["used"] <= result["limit"]


def test_job_counts_from_the_status_files(tmp_path, monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    for job_id, status in [("a", "queued"), ("b", "running"), ("c", "running"), ("d", "done")]:
        (jobs / f"{job_id}.json").write_text(json.dumps({"job_status": status}))
    old = jobs / "old.json"
    old.write_text(json.dumps({"job_status": "running"}))
    os.utime(old, (1, 1))
    (jobs / "broken.json").write_text("{not json")
    assert health.job_counts(str(tmp_path)) == {"queued": 1, "running": 2, "done": 1}


@pytest.fixture
def flask_app(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    job_queue.set_connection(None)
    application = app_module.create_app()
    application.testing = True
    yield application
    job_queue.set_connection(None)


def test_the_route_needs_the_api_key(flask_app):
    assert flask_app.test_client().get("/v1/toolkit/health").status_code == 401


def test_the_route_answers_at_once_with_everything(flask_app, monkeypatch):
    monkeypatch.setattr(health, "cpu_percent", lambda *a, **k: 12.5)
    res = flask_app.test_client().get("/v1/toolkit/health", headers={"X-API-Key": API_KEY})
    assert res.status_code == 200
    body = res.get_json()
    assert body["code"] == 200
    report = body["response"]
    assert set(report) == {
        "build_number", "cpus", "cpu_percent", "memory", "disk", "uptime_seconds", "queue", "jobs",
    }
    assert report["cpu_percent"] == 12.5
    assert report["cpus"] >= 1
    assert report["memory"]["used"] <= report["memory"]["limit"]
    assert report["disk"]["free"] <= report["disk"]["total"]
    # Without Redis each process keeps its own queue, so there is no shared count.
    assert report["queue"] == {"shared": False, "waiting": None, "workers": None, "busy": None}
    assert set(report["jobs"]) == {"queued", "running", "done"}


def test_with_redis_it_counts_the_waiting_jobs_and_the_workers(flask_app, monkeypatch):
    from rq import Worker

    monkeypatch.setenv("REDIS_URL", "redis://fake")
    conn = fakeredis.FakeStrictRedis()
    job_queue.set_connection(conn)
    monkeypatch.setattr(health, "cpu_percent", lambda *a, **k: 0.0)
    job_queue.enqueue("/v1/test/echo", {"value": "x"}, "job-1", 0)
    job_queue.enqueue("/v1/test/echo", {"value": "y"}, "job-2", 0)
    job_queue.save_status("job-1", {"job_status": "queued"})
    job_queue.save_status("job-2", {"job_status": "running"})
    idle = Worker([job_queue.get_queue()], connection=conn, name="idle-one")
    idle.register_birth()
    busy = Worker([job_queue.get_queue()], connection=conn, name="busy-one")
    busy.register_birth()
    busy.set_state("busy")

    res = flask_app.test_client().get("/v1/toolkit/health", headers={"X-API-Key": API_KEY})
    report = res.get_json()["response"]
    assert report["queue"] == {"shared": True, "waiting": 2, "workers": 2, "busy": 1}
    assert report["jobs"] == {"queued": 1, "running": 1, "done": 0}

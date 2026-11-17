"""Worker and thread defaults must follow a container's CPU limit, not the host's cores."""

from services import cpu_limits, whisper_engine


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_cgroup_v2_limit(tmp_path):
    assert cpu_limits.cgroup_cpu_limit(v2_path=write(tmp_path, "cpu.max", "200000 100000\n")) == 2
    assert cpu_limits.cgroup_cpu_limit(v2_path=write(tmp_path, "half", "50000 100000\n")) == 1
    assert cpu_limits.cgroup_cpu_limit(v2_path=write(tmp_path, "odd", "250000 100000\n")) == 3


def test_cgroup_v2_without_limit(tmp_path):
    assert cpu_limits.cgroup_cpu_limit(v2_path=write(tmp_path, "cpu.max", "max 100000\n")) is None


def test_cgroup_v1_limit(tmp_path):
    quota = write(tmp_path, "q", "400000")
    period = write(tmp_path, "p", "100000")
    assert cpu_limits.cgroup_cpu_limit(v2_path=str(tmp_path / "none"), v1_quota=quota, v1_period=period) == 4
    unlimited = write(tmp_path, "u", "-1")
    assert cpu_limits.cgroup_cpu_limit(v2_path=str(tmp_path / "none"), v1_quota=unlimited, v1_period=period) is None


def test_available_cpus_takes_the_smallest(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    monkeypatch.setattr("os.sched_getaffinity", lambda pid: set(range(8)))
    monkeypatch.setattr(cpu_limits, "cgroup_cpu_limit", lambda: 2)
    assert cpu_limits.available_cpus() == 2


def test_whisper_threads_shared_among_queue_workers(monkeypatch):
    monkeypatch.setattr(cpu_limits, "available_cpus", lambda: 4)
    monkeypatch.delenv("WHISPER_CPU_THREADS", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert whisper_engine.cpu_threads() == 4
    monkeypatch.setenv("REDIS_URL", "redis://x:6379/0")
    monkeypatch.setenv("QUEUE_WORKERS", "2")
    assert whisper_engine.cpu_threads() == 2
    monkeypatch.setenv("QUEUE_WORKERS", "8")
    assert whisper_engine.cpu_threads() == 1
    monkeypatch.setenv("WHISPER_CPU_THREADS", "3")
    assert whisper_engine.cpu_threads() == 3


def test_model_cache_falls_back_when_not_writable(monkeypatch, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    monkeypatch.setenv("WHISPER_CACHE_DIR", str(locked))
    assert whisper_engine._download_root() == str(locked)
    monkeypatch.setattr("os.access", lambda path, mode: False)
    assert whisper_engine._download_root() is None
    (locked / "models--Systran--faster-whisper-base").mkdir()
    assert whisper_engine._download_root() == str(locked)  # a model already there is used
    monkeypatch.delenv("WHISPER_CACHE_DIR")
    assert whisper_engine._download_root() is None

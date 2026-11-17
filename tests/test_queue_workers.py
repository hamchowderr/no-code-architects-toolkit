"""
End to end: a real redis-server, worker.py running several worker processes, and the
toolkit's own /v1/code/execute/python route. Proves queued jobs run at the same time.
"""

import os
import subprocess
import sys
import time

import pytest

import app as app_module
from services import job_queue

from conftest import API_KEY, ROOT

SLEEP_SECONDS = 3
JOBS = 3


@pytest.fixture
def workers(redis_server, monkeypatch):
    monkeypatch.setenv("REDIS_URL", redis_server)
    job_queue.set_connection(None)
    env = dict(os.environ, REDIS_URL=redis_server, QUEUE_WORKERS=str(JOBS))
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "worker.py")], cwd=ROOT, env=env)
    # Wait until every worker has registered with Redis.
    from rq import Worker

    deadline = time.time() + 60
    while len(Worker.all(connection=job_queue.get_connection())) < JOBS:
        if time.time() > deadline or proc.poll() is not None:
            proc.kill()
            pytest.fail("worker.py did not start its workers")
        time.sleep(0.2)
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    job_queue.set_connection(None)


def test_queued_jobs_run_in_parallel_on_separate_workers(workers, webhook_sink):
    client = app_module.app.test_client()
    code = f"import time\ntime.sleep({SLEEP_SECONDS})\nprint('slept')"
    started = time.time()
    job_ids = []
    for i in range(JOBS):
        res = client.post(
            "/v1/code/execute/python",
            json={"code": code, "id": f"job-{i}", "webhook_url": webhook_sink.url},
            headers={"X-API-Key": API_KEY},
        )
        assert res.status_code == 202, res.get_json()
        job_ids.append(res.get_json()["job_id"])

    hooks = webhook_sink.wait_for(JOBS, timeout=60)
    elapsed = time.time() - started

    bodies = [h["body"] for h in hooks]
    assert sorted(b["job_id"] for b in bodies) == sorted(job_ids)
    assert all(b["code"] == 200 for b in bodies), bodies
    assert all("slept" in b["response"]["stdout"] for b in bodies)
    # Three different worker processes took the jobs...
    assert len({b["pid"] for b in bodies}) == JOBS
    # ...and ran them at the same time: one after another would take JOBS * SLEEP_SECONDS.
    assert elapsed < SLEEP_SECONDS * JOBS - 2, f"took {elapsed:.1f}s"

    # Status is readable from the API process even though workers ran the jobs.
    status = client.post("/v1/toolkit/job/status", json={"job_id": job_ids[0]}, headers={"X-API-Key": API_KEY})
    assert status.get_json()["response"]["job_status"] == "done"


def test_a_worker_that_dies_is_started_again(workers):
    from rq import Worker

    conn = job_queue.get_connection()
    victim = Worker.all(connection=conn)[0]
    os.kill(victim.pid, 9)
    deadline = time.time() + 30
    while True:
        alive = [w for w in Worker.all(connection=conn) if w.pid != victim.pid]
        if len(alive) >= JOBS:
            break
        assert time.time() < deadline, "the dead worker was not replaced"
        time.sleep(0.5)

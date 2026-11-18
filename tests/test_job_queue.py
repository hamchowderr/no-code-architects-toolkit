"""
The job queue: in-process (no REDIS_URL, the original behaviour) and shared Redis.

These run the toolkit's real request wrapper, a real webhook receiver over HTTP and a real
RQ worker. Redis is fakeredis here; test_queue_workers.py uses a real redis-server.
"""

import time

import fakeredis
import pytest
from flask import Blueprint

import app as app_module
from app_utils import queue_task_wrapper, validate_payload
from services import job_queue
from services.authentication import authenticate

from conftest import API_KEY

ENVELOPE_202_KEYS = {"code", "id", "job_id", "message", "pid", "queue_id", "max_queue_length", "queue_length", "build_number"}
WEBHOOK_KEYS = {"endpoint", "code", "id", "job_id", "response", "message", "pid", "queue_id", "run_time", "queue_time", "total_time", "queue_length", "build_number"}


def make_test_blueprint():
    bp = Blueprint(f"queue_tests_{time.time_ns()}", __name__)

    @bp.route("/v1/test/echo", methods=["POST"], endpoint="echo")
    @authenticate
    @validate_payload({
        "type": "object",
        "properties": {"value": {"type": "string"}, "webhook_url": {"type": "string"}, "id": {"type": "string"}},
        "required": ["value"],
    })
    @queue_task_wrapper(bypass_queue=False)
    def echo(job_id, data):
        return {"value": data["value"], "job_id_seen": job_id}, "/v1/test/echo", 200

    @bp.route("/v1/test/boom", methods=["POST"], endpoint="boom")
    @authenticate
    @queue_task_wrapper(bypass_queue=False)
    def boom(job_id, data):
        raise RuntimeError("the job broke")

    return bp


@pytest.fixture
def flask_app(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    job_queue.set_connection(None)
    application = app_module.create_app()
    application.register_blueprint(make_test_blueprint())
    application.testing = True
    monkeypatch.setattr(job_queue, "_get_app", lambda: application)
    yield application
    job_queue.set_connection(None)


@pytest.fixture
def fake_redis(flask_app):
    conn = fakeredis.FakeStrictRedis()
    job_queue.set_connection(conn)
    yield conn


def post(client, path, body):
    return client.post(path, json=body, headers={"X-API-Key": API_KEY})


def run_worker_until_empty():
    from rq import SimpleWorker

    worker = SimpleWorker([job_queue.get_queue()], connection=job_queue.get_connection())
    worker.work(burst=True)


# --- Without Redis: the original in-process queue ---------------------------------------

def test_without_redis_jobs_use_the_in_process_queue(flask_app, webhook_sink):
    res = post(flask_app.test_client(), "/v1/test/echo", {"value": "a", "id": "run-1", "webhook_url": webhook_sink.url})
    assert res.status_code == 202
    assert set(res.get_json()) == ENVELOPE_202_KEYS
    [hook] = webhook_sink.wait_for(1)
    assert hook["body"]["code"] == 200
    assert hook["body"]["response"]["value"] == "a"
    assert hook["body"]["id"] == "run-1"
    assert set(hook["body"]) == WEBHOOK_KEYS


def test_without_redis_a_failing_job_reports_500_and_the_queue_keeps_going(flask_app, webhook_sink):
    client = flask_app.test_client()
    assert post(client, "/v1/test/boom", {"webhook_url": webhook_sink.url}).status_code == 202
    assert post(client, "/v1/test/echo", {"value": "after", "webhook_url": webhook_sink.url}).status_code == 202
    first, second = webhook_sink.wait_for(2)
    assert first["body"]["code"] == 500
    assert "the job broke" in first["body"]["message"]
    assert second["body"]["code"] == 200
    assert second["body"]["response"]["value"] == "after"


def test_without_a_webhook_the_job_runs_in_the_request(flask_app):
    res = post(flask_app.test_client(), "/v1/test/echo", {"value": "now"})
    assert res.status_code == 200
    assert res.get_json()["response"]["value"] == "now"


# --- With Redis -------------------------------------------------------------------------

def test_with_redis_a_job_is_queued_then_run_by_a_worker(flask_app, fake_redis, webhook_sink):
    client = flask_app.test_client()
    res = post(client, "/v1/test/echo", {"value": "queued", "id": "step-9", "webhook_url": webhook_sink.url})
    assert res.status_code == 202
    body = res.get_json()
    assert set(body) == ENVELOPE_202_KEYS
    assert body["queue_id"] == job_queue.queue_id()
    assert body["queue_length"] == 1
    job_id = body["job_id"]

    status = post(client, "/v1/toolkit/job/status", {"job_id": job_id}).get_json()
    assert status["response"]["job_status"] == "queued"
    assert webhook_sink.received == []  # nothing ran in the API process

    run_worker_until_empty()

    [hook] = webhook_sink.wait_for(1)
    assert set(hook["body"]) == WEBHOOK_KEYS
    assert hook["body"]["code"] == 200
    assert hook["body"]["job_id"] == job_id
    assert hook["body"]["id"] == "step-9"
    assert hook["body"]["response"] == {"value": "queued", "job_id_seen": job_id}
    assert hook["body"]["endpoint"] == "/v1/test/echo"

    status = post(client, "/v1/toolkit/job/status", {"job_id": job_id}).get_json()
    assert status["response"]["job_status"] == "done"
    assert status["response"]["response"]["code"] == 200

    listing = post(client, "/v1/toolkit/jobs/status", {"since_seconds": 60}).get_json()
    assert listing["response"][job_id] == "done"


def test_with_redis_a_failing_job_reports_500(flask_app, fake_redis, webhook_sink):
    res = post(flask_app.test_client(), "/v1/test/boom", {"webhook_url": webhook_sink.url})
    assert res.status_code == 202
    run_worker_until_empty()
    [hook] = webhook_sink.wait_for(1)
    assert hook["body"]["code"] == 500
    assert "the job broke" in hook["body"]["message"]
    assert hook["body"]["response"] is None


def test_with_redis_a_job_that_fails_validation_in_the_worker_still_reports(flask_app, fake_redis, webhook_sink):
    # The API validated this request; here it is run with a payload the route refuses.
    job_queue.enqueue("/v1/test/echo", {"webhook_url": webhook_sink.url, "id": "x"}, "job-bad", time.time())
    run_worker_until_empty()
    [hook] = webhook_sink.wait_for(1)
    assert hook["body"]["code"] == 500
    assert hook["body"]["job_id"] == "job-bad"
    assert hook["body"]["id"] == "x"
    assert "could not start" in hook["body"]["message"]
    assert job_queue.load_status("job-bad")["job_status"] == "done"


def test_with_redis_requests_without_a_webhook_still_answer_directly(flask_app, fake_redis):
    res = post(flask_app.test_client(), "/v1/test/echo", {"value": "sync"})
    assert res.status_code == 200
    assert res.get_json()["response"]["value"] == "sync"
    assert job_queue.queue_length() == 0


def test_with_redis_max_queue_length_is_enforced_across_processes(flask_app, fake_redis, webhook_sink, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_QUEUE_LENGTH", 2)
    client = flask_app.test_client()
    codes = [post(client, "/v1/test/echo", {"value": str(i), "webhook_url": webhook_sink.url}).status_code for i in range(3)]
    assert codes == [202, 202, 429]


def test_when_redis_is_down_jobs_fall_back_to_the_in_process_queue(flask_app, webhook_sink):
    import redis

    job_queue.set_connection(redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2))
    res = post(flask_app.test_client(), "/v1/test/echo", {"value": "fallback", "webhook_url": webhook_sink.url})
    assert res.status_code == 202
    [hook] = webhook_sink.wait_for(1)
    assert hook["body"]["code"] == 200
    assert hook["body"]["response"]["value"] == "fallback"


def test_status_index_drops_jobs_older_than_the_window(fake_redis):
    job_queue.save_status("old", {"job_status": "done"})
    fake_redis.zadd(job_queue.STATUS_INDEX_KEY, {"old": time.time() - 3600})
    job_queue.save_status("new", {"job_status": "running"})
    assert job_queue.recent_statuses(600) == {"new": "running"}


def test_default_worker_count_is_half_the_usable_cpus(monkeypatch):
    from services import cpu_limits

    monkeypatch.setattr(cpu_limits, "available_cpus", lambda: 8)
    assert job_queue.default_worker_count() == 4
    monkeypatch.setattr(cpu_limits, "available_cpus", lambda: 1)
    assert job_queue.default_worker_count() == 1
    monkeypatch.setenv("QUEUE_WORKERS", "3")
    assert job_queue.worker_count() == 3


# --- Jobs a dead worker left behind -----------------------------------------------------

def _left_running(conn, webhook_url, worker_name, job_id="job-left"):
    """A queued echo job that a worker took and then stopped reporting on."""
    from rq.job import Job, JobStatus

    queued_at = time.time() - 120
    job_queue.enqueue("/v1/test/echo", {"value": "x", "id": "step-7", "webhook_url": webhook_url}, job_id, queued_at)
    job = Job.fetch(job_id, connection=conn)
    job.set_status(JobStatus.STARTED)
    job.worker_name = worker_name
    job.save()
    job_queue.save_status(job_id, {"job_status": "running", "job_id": job_id, "queue_id": job_queue.queue_id(), "process_id": 1, "response": None})
    return job_id


def test_a_job_whose_worker_is_gone_is_failed_and_its_webhook_sent(flask_app, fake_redis, webhook_sink):
    job_id = _left_running(fake_redis, webhook_sink.url, "ncat-oldcontainer-13-3")

    assert job_queue.fail_abandoned_jobs() == [job_id]

    [hook] = webhook_sink.wait_for(1)
    assert hook["body"]["code"] == 500
    assert hook["body"]["id"] == "step-7"
    assert hook["body"]["job_id"] == job_id
    assert hook["body"]["message"] == job_queue.ABANDONED_MESSAGE
    status = job_queue.load_status(job_id)
    assert status["job_status"] == "done"
    assert status["response"]["code"] == 500
    # Asked again, it is already settled: no second webhook.
    assert job_queue.fail_abandoned_jobs() == []
    assert len(webhook_sink.received) == 1


def test_a_job_a_live_worker_holds_is_left_alone(flask_app, fake_redis, webhook_sink):
    from rq import Worker

    name = "ncat-thiscontainer-10-0"
    fake_redis.set(Worker.redis_worker_namespace_prefix + name, "alive")
    job_id = _left_running(fake_redis, webhook_sink.url, name)

    assert job_queue.fail_abandoned_jobs() == []
    assert job_queue.load_status(job_id)["job_status"] == "running"
    assert webhook_sink.received == []


def test_a_job_still_waiting_in_the_queue_is_left_alone(flask_app, fake_redis, webhook_sink):
    job_queue.enqueue("/v1/test/echo", {"value": "x", "webhook_url": webhook_sink.url}, "job-waiting", time.time())
    # Some paths write "running" before a worker picks the job up; RQ still has it queued.
    job_queue.save_status("job-waiting", {"job_status": "running", "job_id": "job-waiting"})

    assert job_queue.fail_abandoned_jobs() == []
    run_worker_until_empty()
    [hook] = webhook_sink.wait_for(1)
    assert hook["body"]["code"] == 200


def test_a_running_job_that_never_went_through_the_queue_is_left_alone(flask_app, fake_redis):
    job_queue.save_status("job-in-request", {"job_status": "running", "job_id": "job-in-request"})
    assert job_queue.fail_abandoned_jobs() == []
    assert job_queue.load_status("job-in-request")["job_status"] == "running"

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
Redis-backed job queue.

Without REDIS_URL the toolkit keeps its original behaviour: each Gunicorn worker has an
in-process queue and runs one queued job at a time.

With REDIS_URL set, every request that carries a webhook_url is put on one shared Redis
queue (RQ) and a pool of worker processes (worker.py, QUEUE_WORKERS of them) runs the
jobs in parallel. Workers can live in the same container as the API or in other
containers and machines pointed at the same Redis. Job status is also kept in Redis, so
/v1/toolkit/job/status answers for a job whichever process ran it.

The API does not change: the same 202 answer, the same webhook body, the same job ids.

Settings (environment variables):
  REDIS_URL            e.g. redis://redis:6379/0. Turns the Redis queue on.
  REDIS_QUEUE_NAME     Default "ncat".
  QUEUE_WORKERS        Worker processes worker.py starts. Default: half the CPUs the
                       container may use (its cgroup limit, not the host's count), min 1.
                       Set to 0 on the API container when workers run elsewhere.
  QUEUE_JOB_TIMEOUT    Seconds before a job is stopped. Default 14400 (4 hours).
  JOB_STATUS_TTL       Seconds job status is kept in Redis. Default 604800 (7 days).
  ABANDONED_CHECK_SECONDS  How often worker.py looks for jobs a dead worker left. Default 60.

A job whose worker dies (the container restarts, the process is killed) would otherwise
say "running" forever and never send its webhook. worker.py calls fail_abandoned_jobs()
every minute: a running job whose worker has stopped checking in is marked done with a
failure and its webhook is sent, so whoever asked for it can try again.
"""

import json
import logging
import os
import time
import traceback
import zlib

logger = logging.getLogger(__name__)

STATUS_KEY = "ncat:job:{}"
STATUS_INDEX_KEY = "ncat:jobs"

_connection = None


def redis_url():
    return (os.environ.get("REDIS_URL") or "").strip()


def enabled():
    return bool(redis_url()) or _connection is not None


def queue_name():
    return os.environ.get("REDIS_QUEUE_NAME") or "ncat"


def queue_id():
    """A number that names the shared queue, the same in every process."""
    return zlib.crc32(queue_name().encode())


def job_timeout():
    return int(os.environ.get("QUEUE_JOB_TIMEOUT") or 14400)


def status_ttl():
    return int(os.environ.get("JOB_STATUS_TTL") or 604800)


def default_worker_count():
    from services.cpu_limits import available_cpus

    return max(1, available_cpus() // 2)


def worker_count():
    """QUEUE_WORKERS, or half the CPUs this container may use."""
    raw = os.environ.get("QUEUE_WORKERS")
    return int(raw) if raw not in (None, "") else default_worker_count()


def get_connection():
    global _connection
    if _connection is None:
        import redis

        _connection = redis.Redis.from_url(redis_url())
    return _connection


def set_connection(connection):
    """Used by tests to hand in a fake Redis. None goes back to REDIS_URL."""
    global _connection
    _connection = connection


def get_queue():
    from rq import Queue

    return Queue(queue_name(), connection=get_connection())


def queue_length():
    """Jobs waiting to start (not the ones already running), like Queue.qsize() was."""
    return get_queue().count


def enqueue(path, payload, job_id, queued_at):
    """Puts a request on the shared queue. Raises if Redis cannot be reached."""
    timeout = job_timeout()
    get_queue().enqueue_call(
        run_queued_job,
        args=(path, payload, job_id, queued_at),
        job_id=job_id,
        timeout=timeout,
        # The result already went out by webhook; RQ only needs it briefly.
        result_ttl=3600,
        failure_ttl=status_ttl(),
        description=f"{path} {job_id}",
    )


# --- Job status ------------------------------------------------------------------------

def save_status(job_id, data):
    conn = get_connection()
    pipe = conn.pipeline()
    pipe.set(STATUS_KEY.format(job_id), json.dumps(data, default=str), ex=status_ttl())
    pipe.zadd(STATUS_INDEX_KEY, {job_id: time.time()})
    pipe.zremrangebyscore(STATUS_INDEX_KEY, "-inf", time.time() - status_ttl())
    pipe.execute()


def load_status(job_id):
    raw = get_connection().get(STATUS_KEY.format(job_id))
    return json.loads(raw) if raw else None


def recent_statuses(since_seconds):
    """{job_id: job_status} for jobs updated in the last since_seconds."""
    conn = get_connection()
    ids = conn.zrangebyscore(STATUS_INDEX_KEY, time.time() - since_seconds, "+inf")
    ids = [i.decode() if isinstance(i, bytes) else i for i in ids]
    if not ids:
        return {}
    raws = conn.mget([STATUS_KEY.format(i) for i in ids])
    result = {}
    for job_id, raw in zip(ids, raws):
        if raw:
            data = json.loads(raw)
            if "job_status" in data:
                result[job_id] = data["job_status"]
    return result


# --- Running a job in a worker ---------------------------------------------------------

def _get_app():
    from app import app

    return app


def _failure_response(payload, job_id, path, message, queued_at):
    from version import BUILD_NUMBER

    now = time.time()
    return {
        "endpoint": path,
        "code": 500,
        "id": payload.get("id"),
        "job_id": job_id,
        "response": None,
        "message": message,
        "pid": os.getpid(),
        "queue_id": queue_id(),
        "run_time": 0,
        "queue_time": round(now - queued_at, 3),
        "total_time": round(now - queued_at, 3),
        "queue_length": 0,
        "build_number": BUILD_NUMBER,
    }


def run_queued_job(path, payload, job_id, queued_at):
    """
    Runs one queued request inside a worker process.

    The request goes through the same route, authentication and validation as it did when
    it arrived. queue_task (app.py) sees flask.g.ncat_queued_job and runs it right here,
    then logs the status and sends the webhook exactly as the in-process queue does.
    """
    from flask import g
    from app_utils import log_job_status
    from config import API_KEY
    from services.webhook import send_webhook

    app = _get_app()
    with app.test_request_context(path, method="POST", json=payload, headers={"X-API-Key": API_KEY}):
        g.ncat_queued_job = {"job_id": job_id, "queued_at": queued_at, "handled": False}
        message = None
        try:
            endpoint, view_args = app.url_map.bind("localhost").match(path, method="POST")
            rv = app.view_functions[endpoint](**view_args)
            if not g.ncat_queued_job["handled"]:
                # Authentication or validation answered before the job ran.
                message = f"The job could not start: {_describe(rv)}"
        except Exception as e:
            logger.error(f"Queued job {job_id} failed before it ran: {traceback.format_exc()}")
            message = f"The job could not start: {e}"

        if message is not None:
            response = _failure_response(payload, job_id, path, message, queued_at)
            log_job_status(job_id, {
                "job_status": "done",
                "job_id": job_id,
                "queue_id": queue_id(),
                "process_id": os.getpid(),
                "response": response,
            })
            if payload.get("webhook_url"):
                send_webhook(payload["webhook_url"], response)
            return response
    return {"job_id": job_id}


# --- Jobs left behind by a worker that died ---------------------------------------------

ABANDONED_MESSAGE = (
    "The toolkit stopped while running this job (it was restarted or its worker died), "
    "so the job did not finish. Sending it again should work."
)


def _worker_alive(conn, worker_name):
    from rq import Worker

    if not worker_name:
        return False
    # A worker's key expires when it stops sending heartbeats (worker_ttl, 7 minutes).
    return conn.exists(Worker.redis_worker_namespace_prefix + worker_name) > 0


def fail_abandoned_jobs():
    """
    Finds running jobs whose worker is gone, marks them done with a failure and sends
    their webhook. Returns the ids it failed. Jobs a live worker still holds are left alone.
    """
    from rq.exceptions import NoSuchJobError
    from rq.job import Job, JobStatus
    from app_utils import log_job_status
    from services.webhook import send_webhook

    conn = get_connection()
    failed = []
    ids = conn.zrangebyscore(STATUS_INDEX_KEY, time.time() - status_ttl(), "+inf")
    for raw_id in ids:
        job_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        status = load_status(job_id)
        if not status or status.get("job_status") != "running":
            continue
        try:
            job = Job.fetch(job_id, connection=conn)
        except NoSuchJobError:
            # Not a queued job (it ran inside a web request), or long gone from RQ.
            continue
        rq_status = job.get_status(refresh=True)
        if rq_status in (JobStatus.QUEUED, JobStatus.SCHEDULED, JobStatus.DEFERRED):
            continue
        if rq_status == JobStatus.STARTED and _worker_alive(conn, job.worker_name):
            continue
        if rq_status == JobStatus.FINISHED:
            # The worker finished between our reads; its own status write wins.
            continue

        path, payload, _, queued_at = job.args
        response = _failure_response(payload, job_id, path, ABANDONED_MESSAGE, queued_at)
        log_job_status(job_id, {
            "job_status": "done",
            "job_id": job_id,
            "queue_id": queue_id(),
            "process_id": os.getpid(),
            "response": response,
        })
        logger.warning(f"Job {job_id} ({path}) was left behind by worker {job.worker_name}; marked failed")
        if payload.get("webhook_url"):
            try:
                send_webhook(payload["webhook_url"], response)
            except Exception as e:
                logger.error(f"Could not send the webhook for abandoned job {job_id}: {e}")
        failed.append(job_id)
    return failed


def _describe(rv):
    try:
        body = rv[0] if isinstance(rv, tuple) else rv
        if hasattr(body, "get_json"):
            body = body.get_json(silent=True) or body.get_data(as_text=True)
        return json.dumps(body, default=str)[:500]
    except Exception:
        return str(rv)[:500]

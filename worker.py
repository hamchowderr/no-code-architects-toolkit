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
Runs queued jobs from Redis.

    REDIS_URL=redis://localhost:6379/0 API_KEY=... python worker.py

Starts QUEUE_WORKERS processes (default: half the CPU cores) and restarts any that die.
Each process loads the toolkit once and keeps its Whisper model in memory between jobs.
Run it next to the API (run.sh does this when REDIS_URL is set) or on other machines that
share the same Redis and storage settings.
"""

import logging
import multiprocessing
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s worker %(process)d %(levelname)s %(message)s")
logger = logging.getLogger("ncat.worker")


def run_one_worker(index):
    os.chdir(HERE)  # blueprints are discovered relative to the working directory
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from rq import SimpleWorker
    from services import job_queue
    import app  # noqa: F401  loads every route once, before the first job

    worker = SimpleWorker(
        [job_queue.get_queue()],
        connection=job_queue.get_connection(),
        name=f"ncat-{os.uname().nodename}-{os.getpid()}-{index}",
    )
    logger.info(f"Worker {index} listening on queue '{job_queue.queue_name()}'")
    worker.work(with_scheduler=False)


def worker_count():
    from services import job_queue

    raw = os.environ.get("QUEUE_WORKERS")
    return int(raw) if raw not in (None, "") else job_queue.default_worker_count()


def main():
    if not os.environ.get("REDIS_URL"):
        logger.error("REDIS_URL is not set: there is no queue to work on.")
        return 1
    count = worker_count()
    if count <= 0:
        logger.info("QUEUE_WORKERS is 0: no workers started here.")
        return 0

    ctx = multiprocessing.get_context("spawn")
    procs = {}
    stopping = False

    def start(index):
        p = ctx.Process(target=run_one_worker, args=(index,), name=f"ncat-worker-{index}")
        p.start()
        procs[index] = p

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        for p in procs.values():
            if p.is_alive():
                os.kill(p.pid, signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    logger.info(f"Starting {count} queue worker(s)")
    for i in range(count):
        start(i)

    while not stopping:
        for index, p in list(procs.items()):
            if not p.is_alive() and not stopping:
                logger.warning(f"Worker {index} exited with code {p.exitcode}; starting it again")
                time.sleep(1)
                start(index)
        time.sleep(2)

    for p in procs.values():
        p.join(timeout=60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/bin/bash
# Starts the toolkit.
#
# Without REDIS_URL: Gunicorn only, exactly as before.
# With REDIS_URL: also starts the queue workers (worker.py) that run queued jobs in
# parallel. Set QUEUE_WORKERS=0 when the workers run in another container instead.

cd "$(dirname "$0")"

if [ -n "$REDIS_URL" ] && [ "${QUEUE_WORKERS:-}" != "0" ] && [ -z "$CLOUD_RUN_JOB" ]; then
    python worker.py &
fi

exec gunicorn --bind 0.0.0.0:8080 \
    --workers ${GUNICORN_WORKERS:-2} \
    --timeout ${GUNICORN_TIMEOUT:-300} \
    --worker-class sync \
    --keep-alive 80 \
    --config gunicorn.conf.py \
    app:app

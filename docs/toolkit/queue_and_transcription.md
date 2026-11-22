# Job Queue and Transcription Speed

## Shared Redis queue

By default each Gunicorn worker keeps its own queue and runs one queued job at a time.
A worker that gets three jobs runs them one after another even when the other worker is idle.

Set `REDIS_URL` to use one shared queue instead. Every request with a `webhook_url` goes
on the queue and a pool of worker processes runs the jobs at the same time. The API does
not change: the same `202` answer, the same webhook body, the same job ids, and
`/v1/toolkit/job/status` and `/v1/toolkit/jobs/status` work for jobs run by any worker.
Requests without a `webhook_url` still run directly and answer when done.

The docker-compose files start a `redis` service and set `REDIS_URL` for you.

| Variable | Default | What it does |
|---|---|---|
| `REDIS_URL` | not set | e.g. `redis://redis:6379/0`. Turns the shared queue on. |
| `QUEUE_WORKERS` | half the CPUs the container may use | Worker processes started next to the API. `0` starts none (run `python worker.py` elsewhere). |
| `REDIS_QUEUE_NAME` | `ncat` | Queue name, if several toolkits share one Redis. |
| `QUEUE_JOB_TIMEOUT` | `14400` | Seconds before a job is stopped. |
| `JOB_STATUS_TTL` | `604800` | Seconds job status is kept in Redis. |
| `MAX_QUEUE_LENGTH` | `0` | Now counts the shared queue, so the limit holds across all workers. |

More capacity: run more workers on other machines with the same image, the same
environment (storage keys, `API_KEY`, `REDIS_URL`) and the command `python worker.py`.

If Redis cannot be reached, jobs fall back to the in-process queue rather than failing.

Both defaults read the container's CPU limit (for example `cpus: '2'` in Docker or
Coolify), not the host's core count, so a capped container is not overloaded.

## faster-whisper

Transcription and captions use [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
instead of openai-whisper: the same Whisper models, about 1.4x faster on CPU, less than half
the memory, and no PyTorch in the image. Responses keep the same shape. Each worker loads
the model once and keeps it for later jobs.

| Variable | Default | What it does |
|---|---|---|
| `WHISPER_MODEL` | `base` | Model size (`tiny`, `base`, `small`, `medium`, `large-v3`, ...). |
| `WHISPER_DEVICE` | `auto` | Uses an NVIDIA GPU when CUDA is present, otherwise CPU. |
| `WHISPER_COMPUTE_TYPE` | CPU: `int8` with VNNI (recent Intel), else `float32`. GPU: `float16` | int8 is only quick on processors with VNNI. On the AMD EPYC 7543P that runs our toolkit, float32 was about 1.4x faster. |
| `WHISPER_CPU_THREADS` | half the CPUs, 1 to 4 | Threads per transcription. Past four it got slower, not faster. |
| `WHISPER_BEAM_SIZE` | `1` | Greedy, as before. `5` is a little more accurate and about twice as slow. |
| `WHISPER_VAD_FILTER` | off | `true` skips long silences before decoding. |

Measured on a 4 CPU cloud machine (5 minutes of a NASA podcast, `base` model):
openai-whisper 26.1 s and 1.2 GB peak memory; faster-whisper 18.9 s and 536 MB; 98.4% of
words identical. Four 1-minute transcriptions sent at once: 97 s on the old toolkit
(2 Gunicorn workers), 8.8 s with `REDIS_URL` and 2 queue workers.

On the shared toolkit (AMD EPYC 7543P, 6 CPUs, `base` model, 81 s of speech with word
timings): int8 on one thread, the old default there, took 21.2 s; float32 on three
threads, the new default, 6.6 s; int8 on six threads 27.5 s.

## Tests

    pip install -r requirements.txt fakeredis pytest
    python -m pytest tests

The worker tests need `redis-server` installed; the transcription tests download the
`tiny` model on first run.

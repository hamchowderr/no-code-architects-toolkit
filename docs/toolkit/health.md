# Toolkit Health

`GET /v1/toolkit/health` tells you how busy the toolkit's server is right now. It answers at once and is never queued.

## Request

```bash
curl https://your-toolkit.example/v1/toolkit/health -H "X-API-Key: your_key"
```

No body. The `X-API-Key` header is required.

## Response

```json
{
  "code": 200,
  "response": {
    "build_number": 219,
    "cpus": 4,
    "cpu_percent": 37.5,
    "memory": { "used": 2147483648, "limit": 17179869184 },
    "disk": { "free": 27396825088, "total": 270553174016 },
    "uptime_seconds": 5400,
    "queue": { "shared": true, "waiting": 3, "workers": 4, "busy": 4 },
    "jobs": { "queued": 3, "running": 4, "done": 120 }
  }
}
```

| Field | Meaning |
|---|---|
| `cpus` | CPUs the toolkit may use. In a container with a CPU limit (`cpus: 4` in Docker or Coolify) this is the limit, not the machine's count. |
| `cpu_percent` | How much of those CPUs it is using, 0 to 100, measured over half a second. |
| `memory` | Bytes in use and the limit. In a container both come from the container (its cgroup), leaving out file cache the kernel can drop, the same as `docker stats`. With no limit, the limit is the machine's memory. |
| `disk` | Free and total bytes where the toolkit keeps files while it works on them (`LOCAL_STORAGE_PATH`). |
| `uptime_seconds` | How long this toolkit process has been running. |
| `queue` | With `REDIS_URL` set (`shared: true`): jobs waiting to start, queue workers, and how many are busy. Without Redis each process keeps its own queue, so the counts are `null`. |
| `jobs` | Jobs of the last hour by status, from everyone who uses this toolkit. The health request itself is left out. |

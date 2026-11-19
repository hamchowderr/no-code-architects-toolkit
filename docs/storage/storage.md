# Storage requests (`/v1/storage/*`)

Only in this fork. They let an app that sits in front of the toolkit (such as ncat-dash) use the toolkit's own bucket with nothing but the toolkit's address and API key. The storage keys never leave the toolkit, and signed links go straight to the bucket, so file bytes never pass through the toolkit.

They use the same `S3_*` settings as every upload (`S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_NAME`, `S3_REGION`, optional `S3_PUBLIC_URL` and `S3_LINK_ENDPOINT_URL`). Google Cloud Storage is not supported by these requests.

Every request is a `POST` with the `X-API-Key` header and a JSON body. Each answers at once (no job queue, no webhook):

```json
{ "code": 200, "response": { ... }, "message": "success" }
```

On failure `code` is the HTTP status, `response` is `null` and `message` says what went wrong: `400` bad settings, `401` wrong key, `404` no file at that key, `501` no S3 storage set up on the toolkit, `502` the bucket refused or could not be reached.

| Request | Body | Answer |
|---|---|---|
| `/v1/storage/info` | `{}` | `{provider: "s3", bucket, endpoint, public_url}` (the two addresses the toolkit's output links start with). Fails with 502 if the bucket does not answer, so it doubles as a connection check. |
| `/v1/storage/sign` | `{key, method?: "GET" \| "PUT", expires_in?: seconds (1 to 604800, default 3600), download_name?}` | `{url, method, expires_in}`. A `PUT` link uploads one file (send the bytes as the body). `download_name` makes a `GET` link download under that name. |
| `/v1/storage/list` | `{prefix?, folders?: bool, next_token?, max_keys?: 1 to 1000}` | `{files: [{key, etag, size, last_modified}], folders: [...], next_token}`. With `folders: true` only the files straight inside `prefix` and the folders one level down. `last_modified` is in milliseconds. |
| `/v1/storage/exists` | `{key}` | `{exists}`. A key ending in `/` asks whether anything is in that folder. |
| `/v1/storage/copy` | `{source, destination}` | `{key, etag}`. The bytes stay in the bucket. A file of 4 GiB or more is copied in 512 MiB parts, so there is no 5 GB limit. |
| `/v1/storage/delete` | `{keys: [...]}` (1 to 1000) | `{deleted, failed}`. A key that is already gone counts as deleted. |
| `/v1/storage/folder` | `{prefix}` | `{prefix}`. Makes an empty folder (an empty file whose key ends in `/`). |

## Uploads in parts

One `PUT` stops at 5 GiB. Bigger files (up to almost 5 TiB) go up in parts: start, sign links for the parts, `PUT` each part's bytes to its link (side by side is fine), then finish with every part's ETag.

| Request | Body | Answer |
|---|---|---|
| `/v1/storage/upload/start` | `{key, content_type?}` | `{key, upload_id}` |
| `/v1/storage/upload/sign` | `{key, upload_id, part_numbers: [1, 2, ...] (1 to 1000 per request), expires_in?}` | `{parts: [{part_number, url}], expires_in}`. Each `url` takes one `PUT` of that part's bytes; the bucket answers with an `ETag` header. Part numbers go from 1 to 10,000. |
| `/v1/storage/upload/complete` | `{key, upload_id, parts: [{part_number, etag}]}` | `{key, etag}`. Every part but the last must be at least 5 MiB. A wrong ETag or missing part answers 400; an upload that was cancelled or never started answers 404. |
| `/v1/storage/upload/abort` | `{key, upload_id}` | `{key, cancelled: true}`. Drops the parts already sent. Cancelling twice is fine. |

On R2 each part is one Class A operation, so a 20 GB file in 100 MB parts is about 200 operations.

## CORS

Browsers uploading with a `PUT` link need CORS allowed on the bucket for the app's address (methods `PUT`, `GET`, `HEAD`; header `Content-Type`). For uploads in parts the bucket must also expose the `ETag` header, or the browser cannot read it.

## Example

```bash
curl -X POST https://toolkit.example.com/v1/storage/sign \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"key": "uploads/clip.mp4", "method": "PUT", "expires_in": 3600}'
```

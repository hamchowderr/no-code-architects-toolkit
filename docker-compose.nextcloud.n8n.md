# NCA Toolkit + n8n with Nextcloud storage

Runs the toolkit and n8n next to an existing Nextcloud. Media goes in as Nextcloud
links and results are saved back into a Nextcloud folder with a share link. No
MinIO or other S3 storage is needed.

## What you need

- Docker and Docker Compose
- A Nextcloud you can log in to as admin
- A drive with room for the toolkit's scratch files and n8n's data

## 1. Prepare Nextcloud

1. Create a user for the toolkit (for example `autoedit`). It doesn't need admin rights.
2. Log in as that user, open **Personal settings > Security**, and create an app password.
3. As admin, check **Administration settings > Sharing** allows sharing by link.
4. Let the toolkit's address through. The toolkit reaches a Nextcloud on the same
   machine as `host.docker.internal`, which Nextcloud blocks until you trust it:

   ```bash
   # Nextcloud in Docker:
   docker exec -u www-data <nextcloud-container> php occ config:system:set trusted_domains 10 --value=host.docker.internal
   ```

   Otherwise add `host.docker.internal` to `trusted_domains` in `config/config.php`.
5. Raise Nextcloud's limit on new share links. By default each user may create 20
   links per 10 minutes, and the toolkit creates one per result, so a busy workflow
   hits it quickly. This raises it for that one action only:

   ```bash
   docker exec -u www-data <nextcloud-container> php occ config:system:set ratelimit_overwrite files_sharing.shareapi.createshare user limit --value=1000 --type=integer
   docker exec -u www-data <nextcloud-container> php occ config:system:set ratelimit_overwrite files_sharing.shareapi.createshare user period --value=600 --type=integer
   ```
6. Note the address people use to open Nextcloud. Nextcloud builds share links from
   the address the toolkit used (`host.docker.internal:...`), not from
   `overwrite.cli.url`, so if people use a different address, set it as
   `NEXTCLOUD_PUBLIC_URL` in step 2 and the toolkit returns links with it.

## 2. Configure

```bash
cp .env.nextcloud.n8n.example .env.nextcloud.n8n
```

| Setting | What to put |
| --- | --- |
| `DATA_DIR` | Folder for the stack's own files, e.g. `/Volumes/Pegasus/media-stack` |
| `API_KEY` | A long random key: `openssl rand -hex 32` |
| `NEXTCLOUD_URL` | How the toolkit reaches Nextcloud, e.g. `http://host.docker.internal:8081` |
| `NEXTCLOUD_PUBLIC_URL` | The address people use, if different (e.g. `https://cloud.example.com`) |
| `NEXTCLOUD_USER` / `NEXTCLOUD_APP_PASSWORD` | The user and app password from step 1 |
| `NEXTCLOUD_OUTPUT_FOLDER` | Where results go, e.g. `Auto Edit/Finished` (created if missing) |
| `NEXTCLOUD_LINK_TYPE` | `download` (default): direct file link. `share`: the Nextcloud share page |
| `GENERIC_TIMEZONE` | n8n's time zone, e.g. `America/Los_Angeles` |

On Linux, create the stack's folders and give them to uid 1000 (the user both the
toolkit and n8n run as) before the first start. Otherwise Docker creates them owned
by root, n8n stops with `EACCES: permission denied` and every toolkit call returns
500 Internal Server Error:

```bash
mkdir -p "$DATA_DIR"/{nca-storage,nca-logs,nca-tmp,n8n,n8n-files}
sudo chown -R 1000:1000 "$DATA_DIR"
```

## 3. Start

```bash
docker compose --env-file .env.nextcloud.n8n -f docker-compose.nextcloud.n8n.yml up -d --build
```

- Toolkit: `http://localhost:8080`
- n8n: `http://localhost:5678`
- Inside n8n, the toolkit is `http://ncat:8080`

## 4. Check it

```bash
curl -H "x-api-key: <API_KEY>" http://localhost:8080/v1/toolkit/authenticate
```

Then share a short video in Nextcloud and send the share link:

```bash
curl -X POST http://localhost:8080/v1/media/convert/mp3 \
  -H "x-api-key: <API_KEY>" -H "Content-Type: application/json" \
  -d '{"media_url": "https://cloud.example.com/s/AbC123"}'
```

The MP3 appears in the output folder and the response holds a link to it.

## Nextcloud links the toolkit accepts

Any media URL field takes these, as long as the link is on the configured Nextcloud:

| Link | Example |
| --- | --- |
| Public share | `https://cloud.example.com/s/AbC123` |
| Share, download form | `https://cloud.example.com/s/AbC123/download` |
| File in a shared folder | `https://cloud.example.com/s/AbC123/download?path=/&files=clip.mp4` |
| Private file (uses the app password) | `https://cloud.example.com/remote.php/dav/files/autoedit/Inbox/clip.mp4` |

Password-protected shares can't be fetched automatically; the toolkit says so in
its error. Links on other servers are fetched as ordinary URLs.

## Choosing storage

Without `STORAGE_PROVIDER`, the toolkit uses S3 if `S3_ENDPOINT_URL` is set, then
GCP, then Nextcloud, so existing setups keep working. Set
`STORAGE_PROVIDER=nextcloud` to use Nextcloud even when S3 settings are present.
Reading Nextcloud links only needs `NEXTCLOUD_URL` (plus the user and app password
for private links), so you can read from Nextcloud and save to S3.

## Common errors

| Error says | Fix |
| --- | --- |
| Nextcloud refused the address | Add that address to `trusted_domains` (step 1.4) |
| share is password-protected | Remove the share password, or use a private link |
| rejected the login | Use an app password, not the account password |
| wouldn't create a share link | Allow link sharing (step 1.3) |
| refused to create a share link ... 20 new links | Raise the link limit (step 1.5); the file is already in Nextcloud |
| couldn't find that file or share | The link is wrong, expired or deleted |

## Tests

```bash
pip install -r requirements.txt jsonschema pytest
pytest tests/
```

`tests/fake_nextcloud.py` stands in for Nextcloud, so the tests need no server.

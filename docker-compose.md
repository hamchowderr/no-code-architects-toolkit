# Install No Code Architect Toolkit with Docker

Installation of No Code Architect Toolkit with Docker offers the following advantages:
- Install No Code Architect Toolkit in a clean environment.
- Simplify the setup process.
- Avoid compatibility issues across different operating systems with Docker's consistent environment.

> **Info**  
> If your domain/subdomain is already pointed to the server, start at step 2.  
> If you have already installed Docker and Docker-Compose, start at step 3.

---

## 1. DNS Setup

Point your domain/subdomain to the server. Add an A record to route the domain/subdomain accordingly:

- **Type**: A  
- **Name**: The desired domain/subdomain  
- **IP Address**: `<IP_OF_YOUR_SERVER>`  

---

## 2. Install Docker

This can vary depending on the Linux distribution used. Below are instructions for Ubuntu:

### Set up Docker's APT Repository

```bash
# Add Docker's official GPG key:
sudo apt-get update
sudo apt-get install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to APT sources:
echo \
"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
```

### Install the Docker Packages

```bash
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

---

## 3. Get the Docker Compose File

Clone this repository and work from its folder:

```bash
git clone https://github.com/hamchowderr/no-code-architects-toolkit.git
cd no-code-architects-toolkit
```

[`docker-compose.yml`](docker-compose.yml) starts three services:

- **traefik** answers on ports 80 and 443 and gets a free HTTPS certificate for `APP_DOMAIN` from Let's Encrypt.
- **ncat** is the toolkit, pulled ready-made from `ghcr.io/hamchowderr/no-code-architects-toolkit`. Nothing is compiled on your server. Set `NCAT_IMAGE` in `.env` to pin one commit (`ghcr.io/hamchowderr/no-code-architects-toolkit:sha-<commit>`).
- **redis** holds the shared job queue, so jobs run side by side ([how it works](docs/toolkit/queue_and_transcription.md)).

If your server already has a reverse proxy (Coolify, Caddy, nginx), leave traefik out and point the proxy at the toolkit's port 8080.

---

## 4. Create `.env` File

Create an `.env` file and configure it accordingly:

```env
# The name of your application.
APP_NAME=NCAToolkit

# Debug mode setting. Set to `false` for production environments.
APP_DEBUG=false

# Your app's domain or subdomain, without the 'http://' or 'https://' prefix.
APP_DOMAIN=example.com

# Full application URL is automatically configured; no modification required.
APP_URL=https://${APP_DOMAIN}

# SSL settings
SSL_EMAIL=user@example.com

# API_KEY
# Purpose: Used for API authentication.
# Requirement: Mandatory.
API_KEY=your_api_key_here

# s3 Compatible Storage Env Vars
#
#S3_ACCESS_KEY=your_access_key
#S3_SECRET_KEY=your_secret_key
#S3_ENDPOINT_URL=https://your-endpoint-url
#S3_REGION=your-region
#S3_BUCKET_NAME=your-bucket-name


# Google Cloud Storage Env Variables
#
# GCP_SA_CREDENTIALS
# Purpose: The JSON credentials for the GCP Service Account.
# Requirement: Mandatory if using GCP storage.
#GCP_SA_CREDENTIALS=/path/to/your/gcp/service_account.json

# GCP_BUCKET_NAME
# Purpose: The name of the GCP storage bucket.
# Requirement: Mandatory if using GCP storage.
#GCP_BUCKET_NAME=your_gcp_bucket_name

# Optional: pin the toolkit to one commit's image
#NCAT_IMAGE=ghcr.io/hamchowderr/no-code-architects-toolkit:sha-<commit>

```

---

## 5. Start Docker Compose

Start No Code Architect Toolkit  using the following command:

```bash
docker compose up -d
```

To view logs in real time:

```bash
docker compose logs -f
```

To stop the containers:

```bash
docker compose stop
```

To restart and reload env vars, update `.env`, then:

```bash
docker compose up -d --force-recreate ncat
```

To update to a newer toolkit, move `NCAT_IMAGE` to the new commit (or keep `latest`), then:

```bash
docker compose pull ncat
docker compose up -d ncat
```

---

## 6. Done

No Code Architect Toolkit is now accessible through the specified APP_URL. For example:  
[https://example.com](https://example.com)

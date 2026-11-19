"""
Streaming a file from a link into the bucket (/v1/s3/upload's service), against a real S3
server over HTTP (moto's server). The parts must come out the same size however the
download arrives, because Cloudflare R2 refuses an upload whose parts differ.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from services.v1.s3 import upload as s3_upload

BUCKET = "ncat-upload-test"
PART = 5 * 1024 * 1024  # the smallest part S3 takes
DATA = os.urandom(2 * PART + 1234567)


@pytest.fixture(scope="module")
def s3_server():
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture(scope="module")
def source():
    """Sends DATA in pieces of uneven sizes, and remembers the headers it was sent."""
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            seen["authorization"] = self.headers.get("Authorization")
            if self.path == "/missing":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            at, sizes, i = 0, [333_333, 1_048_577, 77, 2_500_001], 0
            while at < len(DATA):
                piece = DATA[at : at + sizes[i % len(sizes)]]
                self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                at += len(piece)
                i += 1
            self.wfile.write(b"0\r\n\r\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


@pytest.fixture
def bucket(s3_server, monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT_URL", s3_server)
    monkeypatch.setenv("S3_ACCESS_KEY", "test")
    monkeypatch.setenv("S3_SECRET_KEY", "test")
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET)
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setattr(s3_upload, "PART_SIZE", PART)
    requests.post(f"{s3_server}/moto-api/reset")
    client = s3_upload.get_s3_client()
    client.create_bucket(Bucket=BUCKET)
    sizes = []
    real = s3_upload._upload_part

    def counting(s3_client, bucket_name, key, upload_id, parts, body):
        sizes.append(len(body))
        return real(s3_client, bucket_name, key, upload_id, parts, body)

    monkeypatch.setattr(s3_upload, "_upload_part", counting)
    return client, sizes


def test_parts_are_the_same_size_however_the_download_arrives(bucket, source):
    client, sizes = bucket
    url, seen = source
    result = s3_upload.stream_upload_to_s3(
        f"{url}/meeting.mp4",
        "teams/t1/uploads/u1/Weekly Sync.mp4",
        download_headers={"Authorization": "Bearer zoom-token"},
    )
    assert result["filename"] == "teams/t1/uploads/u1/Weekly Sync.mp4"
    assert seen["authorization"] == "Bearer zoom-token"
    assert sizes == [PART, PART, 1234567]
    stored = client.get_object(Bucket=BUCKET, Key="teams/t1/uploads/u1/Weekly Sync.mp4")
    assert stored["Body"].read() == DATA
    assert stored["ContentType"] == "video/mp4"


def test_a_failed_download_leaves_no_upload_behind(bucket, source):
    client, _ = bucket
    url, _ = source
    with pytest.raises(requests.HTTPError):
        s3_upload.stream_upload_to_s3(f"{url}/missing", "gone.mp4")
    assert client.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) == []
    assert client.list_objects_v2(Bucket=BUCKET).get("KeyCount", 0) == 0

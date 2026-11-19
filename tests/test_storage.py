"""
The toolkit's own bucket requests (/v1/storage/*), run against a real S3 server over HTTP
(moto's server). Signed links are used with plain HTTP the way a browser would.
"""

import pytest
import requests

import app as app_module

from conftest import API_KEY

BUCKET = "ncat-test"


@pytest.fixture(scope="module")
def s3_server():
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def client(s3_server, monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT_URL", s3_server)
    monkeypatch.setenv("S3_ACCESS_KEY", "test")
    monkeypatch.setenv("S3_SECRET_KEY", "test")
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET)
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.delenv("S3_PUBLIC_URL", raising=False)
    monkeypatch.delenv("S3_LINK_ENDPOINT_URL", raising=False)
    requests.post(f"{s3_server}/moto-api/reset")
    from services.s3_toolkit import get_s3_client

    get_s3_client(s3_server, "test", "test", "us-east-1").create_bucket(Bucket=BUCKET)
    application = app_module.create_app()
    application.testing = True
    return application.test_client()


def call(client, path, body=None, key=API_KEY):
    res = client.post(f"/v1/storage/{path}", json=body or {}, headers={"X-API-Key": key})
    return res.status_code, res.get_json()


def upload(client, key, data):
    status, body = call(client, "sign", {"key": key, "method": "PUT", "expires_in": 600})
    assert status == 200, body
    put = requests.put(body["response"]["url"], data=data, headers={"Content-Type": "video/mp4"})
    assert put.status_code == 200, put.text


def test_every_request_needs_the_api_key(client):
    for path in ["info", "sign", "list", "exists", "copy", "delete", "folder",
                 "upload/start", "upload/sign", "upload/complete", "upload/abort"]:
        status, _ = call(client, path, {"key": "a"}, key="wrong")
        assert status == 401, path


def test_info_names_the_bucket_and_public_address(client, s3_server, monkeypatch):
    status, body = call(client, "info")
    assert status == 200
    assert body["response"] == {"provider": "s3", "bucket": BUCKET, "endpoint": s3_server, "public_url": None}
    monkeypatch.setenv("S3_PUBLIC_URL", "https://files.example.com/")
    assert call(client, "info")[1]["response"]["public_url"] == "https://files.example.com"


def test_info_fails_when_the_bucket_does_not_answer(client, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "no-such-bucket")
    status, body = call(client, "info")
    assert status == 502
    assert "does not exist" in body["message"]


def test_info_says_when_no_s3_storage_is_set(client, monkeypatch):
    monkeypatch.delenv("S3_ENDPOINT_URL")
    status, body = call(client, "info")
    assert status == 501
    assert "S3_ENDPOINT_URL" in body["message"]


def test_a_signed_put_uploads_and_a_signed_get_reads_it_back(client):
    upload(client, "uploads/team/clip one.mp4", b"video bytes")
    status, body = call(client, "sign", {"key": "uploads/team/clip one.mp4"})
    assert status == 200
    got = requests.get(body["response"]["url"])
    assert got.status_code == 200 and got.content == b"video bytes"


def test_a_download_link_names_the_file(client):
    upload(client, "a/b.mp4", b"x")
    _, body = call(client, "sign", {"key": "a/b.mp4", "download_name": 'My "Clip".mp4'})
    got = requests.get(body["response"]["url"])
    assert got.headers["Content-Disposition"] == 'attachment; filename="My Clip.mp4"'


def test_sign_refuses_bad_settings(client):
    assert call(client, "sign", {"key": "a", "method": "DELETE"})[0] == 400
    assert call(client, "sign", {"key": "a", "expires_in": 8 * 24 * 3600})[0] == 400
    assert call(client, "sign", {"key": "/a"})[0] == 400
    assert call(client, "sign", {})[0] == 400


def test_links_are_made_for_the_link_address(client, monkeypatch):
    monkeypatch.setenv("S3_LINK_ENDPOINT_URL", "https://files.example.com")
    _, body = call(client, "sign", {"key": "a.mp4"})
    assert body["response"]["url"].startswith("https://files.example.com/")


def test_list_reads_a_flat_bucket_as_folders(client):
    for key in ["shows/ep1.mp4", "shows/ep2.mp4", "shows/raw/take.mp4", "top.mp4"]:
        upload(client, key, b"12345")
    _, body = call(client, "list", {"prefix": "shows/", "folders": True})
    page = body["response"]
    assert [f["key"] for f in page["files"]] == ["shows/ep1.mp4", "shows/ep2.mp4"]
    assert page["folders"] == ["shows/raw/"]
    assert page["files"][0]["size"] == 5 and page["files"][0]["etag"]
    assert page["files"][0]["last_modified"] > 1_600_000_000_000
    _, body = call(client, "list", {"prefix": "shows/"})
    assert len(body["response"]["files"]) == 3


def test_list_pages_through_with_next_token(client):
    for n in range(3):
        upload(client, f"p/{n}.mp4", b"x")
    _, first = call(client, "list", {"prefix": "p/", "max_keys": 2})
    assert len(first["response"]["files"]) == 2 and first["response"]["next_token"]
    _, second = call(client, "list", {"prefix": "p/", "max_keys": 2,
                                      "next_token": first["response"]["next_token"]})
    assert [f["key"] for f in second["response"]["files"]] == ["p/2.mp4"]
    assert second["response"]["next_token"] is None


def test_exists_for_files_and_folders(client):
    upload(client, "f/one.mp4", b"x")
    assert call(client, "exists", {"key": "f/one.mp4"})[1]["response"] == {"exists": True}
    assert call(client, "exists", {"key": "f/two.mp4"})[1]["response"] == {"exists": False}
    assert call(client, "exists", {"key": "f/"})[1]["response"] == {"exists": True}
    assert call(client, "exists", {"key": "g/"})[1]["response"] == {"exists": False}


def test_copy_then_delete_is_a_move(client):
    upload(client, "old/name.mp4", b"keep me")
    status, body = call(client, "copy", {"source": "old/name.mp4", "destination": "new/name.mp4"})
    assert status == 200 and body["response"]["etag"]
    _, body = call(client, "delete", {"keys": ["old/name.mp4", "already/gone.mp4"]})
    assert body["response"] == {"deleted": ["old/name.mp4", "already/gone.mp4"], "failed": []}
    assert call(client, "exists", {"key": "old/name.mp4"})[1]["response"]["exists"] is False
    _, body = call(client, "sign", {"key": "new/name.mp4"})
    assert requests.get(body["response"]["url"]).content == b"keep me"


def test_copy_of_a_missing_file_says_so(client):
    status, body = call(client, "copy", {"source": "nope.mp4", "destination": "x.mp4"})
    assert status == 404
    assert body["message"] == "No file at that key."


def test_folder_makes_an_empty_folder(client):
    status, body = call(client, "folder", {"prefix": "empty"})
    assert status == 200 and body["response"] == {"prefix": "empty/"}
    _, body = call(client, "list", {"folders": True})
    assert "empty/" in body["response"]["folders"]


MIB = 1024 * 1024


def send_in_parts(client, key, chunks):
    """Starts an upload in parts, PUTs every chunk to its signed link the way a browser does,
    and gives back the parts with the ETag the bucket answered each one with."""
    status, body = call(client, "upload/start", {"key": key, "content_type": "video/mp4"})
    assert status == 200, body
    upload_id = body["response"]["upload_id"]
    status, body = call(client, "upload/sign", {
        "key": key, "upload_id": upload_id, "part_numbers": list(range(1, len(chunks) + 1)),
    })
    assert status == 200, body
    parts = []
    for link, chunk in zip(body["response"]["parts"], chunks):
        put = requests.put(link["url"], data=chunk)
        assert put.status_code == 200, put.text
        parts.append({"part_number": link["part_number"], "etag": put.headers["ETag"]})
    return upload_id, parts


def test_an_upload_in_parts_becomes_one_file(client, s3_server):
    chunks = [b"a" * (5 * MIB), b"b" * (5 * MIB), b"c" * 1000]
    upload_id, parts = send_in_parts(client, "uploads/big.mp4", chunks)
    # Out of order on purpose: the toolkit sorts them.
    status, body = call(client, "upload/complete", {
        "key": "uploads/big.mp4", "upload_id": upload_id, "parts": list(reversed(parts)),
    })
    assert status == 200, body
    assert body["response"]["key"] == "uploads/big.mp4"
    assert body["response"]["etag"]
    status, body = call(client, "sign", {"key": "uploads/big.mp4"})
    got = requests.get(body["response"]["url"])
    assert got.content == b"".join(chunks)
    assert got.headers["Content-Type"] == "video/mp4"


def test_signing_parts_checks_the_numbers(client):
    status, body = call(client, "upload/start", {"key": "x.mp4"})
    upload_id = body["response"]["upload_id"]
    for numbers in [[], [0], [10001]]:
        status, _ = call(client, "upload/sign", {"key": "x.mp4", "upload_id": upload_id, "part_numbers": numbers})
        assert status == 400, numbers
    status, body = call(client, "upload/sign", {"key": "x.mp4", "upload_id": upload_id, "part_numbers": [1, 2]})
    assert status == 200
    assert [part["part_number"] for part in body["response"]["parts"]] == [1, 2]


def test_completing_with_a_wrong_etag_says_so(client):
    upload_id, parts = send_in_parts(client, "bad.mp4", [b"a" * (5 * MIB), b"b"])
    parts[0]["etag"] = '"0000"'
    status, body = call(client, "upload/complete", {"key": "bad.mp4", "upload_id": upload_id, "parts": parts})
    assert status == 400
    assert "etag" in body["message"]


def test_a_part_number_listed_twice_is_refused(client):
    upload_id, parts = send_in_parts(client, "twice.mp4", [b"a"])
    status, body = call(client, "upload/complete", {
        "key": "twice.mp4", "upload_id": upload_id, "parts": parts + parts,
    })
    assert status == 400
    assert "twice" in body["message"]


def test_a_cancelled_upload_leaves_no_file(client):
    upload_id, parts = send_in_parts(client, "gone.mp4", [b"a"])
    status, body = call(client, "upload/abort", {"key": "gone.mp4", "upload_id": upload_id})
    assert status == 200
    assert body["response"]["cancelled"] is True
    # Cancelling again is fine.
    assert call(client, "upload/abort", {"key": "gone.mp4", "upload_id": upload_id})[0] == 200
    assert call(client, "exists", {"key": "gone.mp4"})[1]["response"]["exists"] is False


def test_a_big_file_is_copied_in_parts(client, monkeypatch):
    from services.v1.storage import bucket

    # The real line is 4 GiB; a small one here so the test sends 11 MiB, not 4 GiB.
    monkeypatch.setattr(bucket, "COPY_IN_PARTS_FROM", 6 * MIB)
    monkeypatch.setattr(bucket, "COPY_PART_SIZE", 5 * MIB)
    chunks = [b"a" * (5 * MIB), b"b" * (5 * MIB), b"c" * MIB]
    upload_id, parts = send_in_parts(client, "big/source.mp4", chunks)
    call(client, "upload/complete", {"key": "big/source.mp4", "upload_id": upload_id, "parts": parts})
    status, body = call(client, "copy", {"source": "big/source.mp4", "destination": "big/copy.mp4"})
    assert status == 200, body
    assert body["response"]["key"] == "big/copy.mp4"
    assert body["response"]["etag"].endswith("-3")  # made of three parts
    status, body = call(client, "sign", {"key": "big/copy.mp4"})
    assert requests.get(body["response"]["url"]).content == b"".join(chunks)


def test_bucket_upload_errors_read_plainly():
    # moto answers an unknown upload id with a server error, so the bucket's real answers are made here.
    from botocore.exceptions import ClientError
    from services.v1.storage.bucket import _upload_error

    def answer(code):
        return _upload_error(ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": 400}}, "x"), "finish")

    assert answer("NoSuchUpload").status == 404
    assert "never started" in str(answer("NoSuchUpload"))
    assert answer("InvalidPart").status == 400
    assert "5 MiB" in str(answer("EntityTooSmall"))

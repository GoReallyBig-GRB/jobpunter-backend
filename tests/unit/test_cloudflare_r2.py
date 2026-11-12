from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread
from urllib.parse import unquote, urlsplit

import pytest
from botocore.exceptions import ClientError

from app.infrastructure.storage.cloudflare_r2 import CloudflareR2StorageAdapter


class FakeR2Client:
    def __init__(self):
        self.objects = {}
        self.last_put = None
        self.last_presign = None

    def put_object(self, **kwargs):
        self.last_put = kwargs
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.objects:
            raise RuntimeError("precondition failed: object already exists")
        self.objects[key] = {
            "Body": kwargs["Body"],
            "ContentType": kwargs["ContentType"],
            "Metadata": kwargs["Metadata"],
        }

    def get_object(self, **kwargs):
        return {"Body": BytesIO(self.objects[(kwargs["Bucket"], kwargs["Key"])]["Body"])}

    def delete_object(self, **kwargs):
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.last_presign = (operation, Params, ExpiresIn)
        return "https://private-r2.example.test/signed"


class FakeS3Handler(BaseHTTPRequestHandler):
    def _bucket_and_key(self):
        path = unquote(urlsplit(self.path).path).lstrip("/")
        bucket, _, key = path.partition("/")
        return bucket, key

    def do_PUT(self):
        key = self._bucket_and_key()
        self.server.objects[key] = self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("ETag", '"fake-etag"')
        self.end_headers()

    def do_GET(self):
        key = self._bucket_and_key()
        content = self.server.objects.get(key)
        if content is None:
            content = (
                b"<Error><Code>NoSuchKey</Code><Message>Object not found</Message>"
                b"<RequestId>fake-request</RequestId></Error>"
            )
            self.send_response(404)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_DELETE(self):
        self.server.objects.pop(self._bucket_and_key(), None)
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format, *_args):
        pass


@pytest.fixture
def fake_s3_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3Handler)
    server.objects = {}
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _adapter(client):
    return CloudflareR2StorageAdapter(
        bucket_name="private-documents",
        endpoint_url="https://account.r2.cloudflarestorage.com",
        access_key_id="test-key",
        secret_access_key="test-secret",
        client=client,
    )


def test_r2_adapter_stores_private_bytes_with_checksum_and_no_overwrite():
    client = FakeR2Client()
    storage = _adapter(client)
    content = b"candidate document bytes"
    stored = storage.put("candidates/c1/imports/d1/cv.pdf", content, "application/pdf")

    assert stored.storage_key == "candidates/c1/imports/d1/cv.pdf"
    assert stored.checksum_sha256 == sha256(content).hexdigest()
    assert stored.byte_size == len(content)
    assert client.last_put["IfNoneMatch"] == "*"
    assert client.last_put["Metadata"]["sha256"] == sha256(content).hexdigest()
    assert client.last_put["Bucket"] == "private-documents"
    assert storage.get(stored.storage_key) == content
    with pytest.raises(RuntimeError, match="precondition failed"):
        storage.put(stored.storage_key, b"overwrite", "application/pdf")


def test_r2_adapter_round_trips_through_fake_s3_endpoint(fake_s3_endpoint):
    storage = CloudflareR2StorageAdapter(
        bucket_name="private-documents",
        endpoint_url=fake_s3_endpoint,
        access_key_id="test-key",
        secret_access_key="test-secret",
    )
    key = "candidates/c1/imports/d1/cv.pdf"
    content = b"private document bytes"

    stored = storage.put(key, content, "application/pdf")

    assert stored.checksum_sha256 == sha256(content).hexdigest()
    assert storage.get(key) == content
    storage.delete(key)
    with pytest.raises(ClientError) as error:
        storage.get(key)
    assert error.value.response["Error"]["Code"] == "NoSuchKey"


def test_r2_adapter_deletes_and_creates_expiring_download_url():
    client = FakeR2Client()
    storage = _adapter(client)
    key = "candidates/c1/imports/d1/cv.pdf"
    storage.put(key, b"%PDF-test", "application/pdf")

    assert storage.create_download_url(key, 300) == "https://private-r2.example.test/signed"
    assert client.last_presign == (
        "get_object",
        {"Bucket": "private-documents", "Key": key},
        300,
    )
    storage.delete(key)
    assert ("private-documents", key) not in client.objects


@pytest.mark.parametrize("key", ["", "/absolute/key", "../escape", "a/../escape", "a\\\\b"])
def test_r2_adapter_rejects_unsafe_object_keys(key):
    with pytest.raises(ValueError, match="normalized private object key"):
        _adapter(FakeR2Client()).put(key, b"data", "application/octet-stream")


@pytest.mark.parametrize("ttl", [0, -1, 3601, True, 1.5])
def test_r2_adapter_rejects_invalid_download_url_ttl(ttl):
    with pytest.raises(ValueError, match="between 1 and 3600"):
        _adapter(FakeR2Client()).create_download_url("candidates/c1/cv.pdf", ttl)

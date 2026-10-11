from hashlib import sha256
from io import BytesIO

import pytest

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

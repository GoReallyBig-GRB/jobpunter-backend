from datetime import timedelta
from hashlib import sha256

import pytest

from app.infrastructure.storage.google_cloud_storage import GoogleCloudStorageAdapter


class FakeBlob:
    def __init__(self, key):
        self.key = key
        self.content = None
        self.content_type = None
        self.upload_options = None
        self.deleted = False
        self.signed_url_options = None

    def upload_from_string(self, content, **kwargs):
        self.content = content
        self.content_type = kwargs["content_type"]
        self.upload_options = kwargs

    def download_as_bytes(self):
        return self.content

    def delete(self):
        self.deleted = True

    def generate_signed_url(self, **kwargs):
        self.signed_url_options = kwargs
        return f"https://signed.example.test/{self.key}"


class FakeBucket:
    def __init__(self):
        self.blobs = {}

    def blob(self, key):
        return self.blobs.setdefault(key, FakeBlob(key))


class FakeClient:
    def __init__(self):
        self._bucket = FakeBucket()
        self.bucket_name = None

    def bucket(self, name):
        self.bucket_name = name
        return self._bucket


def test_gcs_adapter_stores_private_bytes_with_checksum_and_no_overwrite():
    client = FakeClient()
    storage = GoogleCloudStorageAdapter("private-documents", client=client)
    content = b"private candidate document"
    result = storage.put("candidates/c1/documents/d1.docx", content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    assert client.bucket_name == "private-documents"
    assert result.storage_key == "candidates/c1/documents/d1.docx"
    assert result.checksum_sha256 == sha256(content).hexdigest()
    assert result.byte_size == len(content)
    blob = client._bucket.blobs[result.storage_key]
    assert blob.content == content
    assert blob.content_type.startswith("application/vnd.openxmlformats")
    assert blob.upload_options["if_generation_match"] == 0
    assert "make_public" not in dir(blob)


def test_gcs_adapter_reads_deletes_and_creates_time_limited_signed_urls():
    client = FakeClient()
    storage = GoogleCloudStorageAdapter("private-documents", client=client)
    key = "candidates/c1/documents/d1.pdf"
    storage.put(key, b"%PDF-test", "application/pdf")

    assert storage.get(key) == b"%PDF-test"
    url = storage.create_download_url(key, 300)
    assert url == f"https://signed.example.test/{key}"
    options = client._bucket.blobs[key].signed_url_options
    assert options["version"] == "v4"
    assert options["expiration"] == timedelta(seconds=300)
    assert options["method"] == "GET"

    storage.delete(key)
    assert client._bucket.blobs[key].deleted is True


@pytest.mark.parametrize("key", ["", "/absolute/key", "../escape", "a/../escape", "a\\b"])
def test_gcs_adapter_rejects_unsafe_object_keys(key):
    storage = GoogleCloudStorageAdapter("private-documents", client=FakeClient())
    with pytest.raises(ValueError, match="normalized private object key"):
        storage.put(key, b"data", "application/octet-stream")


@pytest.mark.parametrize("ttl", [0, -1, 3601, True, 1.5])
def test_gcs_adapter_rejects_invalid_signed_url_ttl(ttl):
    storage = GoogleCloudStorageAdapter("private-documents", client=FakeClient())
    with pytest.raises(ValueError, match="between 1 and 3600"):
        storage.create_download_url("candidates/c1/documents/d1.pdf", ttl)

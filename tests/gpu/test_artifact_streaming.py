import hashlib

import pytest
from app.gpu.artifacts import ArtifactError, LocalArtifactStore


@pytest.mark.parametrize("megabytes", [150, 500])
def test_large_object_resume_without_base64(tmp_path, megabytes):
    block = b"\0" * (1024 * 1024)
    sha = hashlib.sha256()
    for _ in range(megabytes):
        sha.update(block)
    storage = LocalArtifactStore(tmp_path)
    item = storage.begin(
        "owner", "execution", "mock-video.mp4", "video/mp4", megabytes * len(block), sha.hexdigest()
    )
    key = item["artifact_id"]
    split = megabytes * 3 // 5
    for index in range(split):
        storage.append(key, "owner", "execution", index * len(block), block)
    # A new adapter recovers the offset from durable bytes, without original process memory.
    storage = LocalArtifactStore(tmp_path)
    assert storage.offset(key, "owner", "execution") == split * len(block)
    for index in range(split, megabytes):
        storage.append(key, "owner", "execution", index * len(block), block)
    verified = storage.verify(key, "owner", "execution")
    assert verified["upload_state"] == "verified"
    assert "content" not in verified and "content_b64" not in verified
    assert storage.verify_manifest(verified, "owner", "execution")[0] == verified
    with pytest.raises(ArtifactError, match="ownership"):
        storage.verify(key, "other", "execution")
    assert (
        storage.begin(
            "owner", "execution", "mock-video.mp4", "video/mp4", megabytes * len(block), sha.hexdigest()
        )
        == verified
    )


def test_upload_replay_conflict_and_checksum(tmp_path):
    storage = LocalArtifactStore(tmp_path)
    item = storage.begin("owner", "execution", "a.png", "image/png", 4, hashlib.sha256(b"abcd").hexdigest())
    key = item["artifact_id"]
    assert storage.append(key, "owner", "execution", 0, b"ab") == 2
    assert storage.append(key, "owner", "execution", 0, b"ab") == 2
    with pytest.raises(ArtifactError, match="conflict"):
        storage.append(key, "owner", "execution", 0, b"xx")
    with pytest.raises(ArtifactError, match="checksum"):
        storage.verify(key, "owner", "execution")
    with pytest.raises(ArtifactError, match="opaque"):
        storage.offset("../bad", "owner", "execution")


def test_two_store_instances_replay_same_chunk_without_duplication(tmp_path):
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    from app.gpu.artifacts import LocalArtifactStore
    data=b'parallel-chunk'*1000
    first=LocalArtifactStore(tmp_path)
    item=first.begin('u','e','parallel.png','image/png',len(data),hashlib.sha256(data).hexdigest())
    key=item['artifact_id']
    def append(_):
        return LocalArtifactStore(tmp_path).append(key,'u','e',0,data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(append,range(2)))==[len(data),len(data)]
    assert first.offset(key,'u','e')==len(data)
    assert first.verify(key,'u','e')['upload_state']=='verified'

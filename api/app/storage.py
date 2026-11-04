"""Private durable asset files; no executable uploads or user paths."""

import hashlib
import io
import mimetypes
import re
import uuid
from pathlib import Path

from PIL import Image

from .config import settings

ALLOWED = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/webm",
    "audio/wav",
    "audio/mpeg",
}


def asset_path(asset):
    root = (settings.data_dir / "assets").resolve()
    key = asset.get("storage_key", "")
    path = (root / key).resolve()
    if not key or not path.is_relative_to(root) or path == root:
        raise ValueError("非法素材路径")
    return path


def save_asset(store, owner_id, project_id, content, filename, source_job_id=None, metadata=None):
    if not content or len(content) > settings.max_upload_bytes:
        raise ValueError("文件为空或超过大小限制")
    name = re.sub(r"[^\w.()\-\u4e00-\u9fff ]+", "_", Path(filename).name).strip(" .")[:140] or "file.bin"
    mime = mimetypes.guess_type(name)[0]
    if mime not in ALLOWED:
        if mime != "application/json" or not (metadata or {}).get("system_smoke"):
            raise ValueError("不支持的素材类型")
        __import__("json").loads(content)  # Diagnostic JSON only from the trusted CUDA executor.
    width = height = None
    if mime.startswith("image/"):
        with Image.open(io.BytesIO(content)) as im:
            im.verify()
        with Image.open(io.BytesIO(content)) as im:
            width, height = im.size
            if Image.MIME.get(im.format) != mime:
                raise ValueError("图片扩展名与内容不符")
    elif mime == "video/mp4" and content[4:8] != b"ftyp":
        raise ValueError("MP4内容无效")
    elif mime == "video/webm" and not content.startswith(b"\x1a\x45\xdf\xa3"):
        raise ValueError("WebM内容无效")
    elif mime == "audio/wav" and not (content[:4] == b"RIFF" and content[8:12] == b"WAVE"):
        raise ValueError("WAV内容无效")
    elif mime == "audio/mpeg" and not (
        content.startswith(b"ID3") or content[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    ):
        raise ValueError("MP3内容无效")
    key = f"{owner_id}/{project_id}/{uuid.uuid4().hex}-{name}"
    row = {
        "filename": name,
        "mime": mime,
        "storage_key": key,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "width": width,
        "height": height,
        "favorite": False,
        "source_job_id": source_job_id,
    }
    for k, v in (metadata or {}).items():
        if k not in row and k not in {"id", "owner_id", "project_id"}:
            row[k] = v
    target = asset_path(row)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".partial")
    tmp.write_bytes(content)
    tmp.replace(target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != row["sha256"]:
        raise OSError("素材落盘校验失败")
    return store.create("assets", owner_id, row, project_id=project_id)


def read_asset(asset):
    return asset_path(asset).read_bytes()


def delete_asset_file(asset):
    asset_path(asset).unlink(missing_ok=True)

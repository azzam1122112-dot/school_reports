"""Upload an extracted Django MEDIA_ROOT to an S3-compatible bucket."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

import boto3


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


media_root = Path(os.environ.get("MEDIA_IMPORT_ROOT", "/import-media/media"))
if not media_root.is_dir():
    raise RuntimeError(f"Media import directory does not exist: {media_root}")

bucket = required_env("R2_BUCKET_NAME")
client = boto3.client(
    "s3",
    endpoint_url=required_env("R2_ENDPOINT_URL"),
    aws_access_key_id=required_env("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=required_env("R2_SECRET_ACCESS_KEY"),
    region_name=os.environ.get("AWS_S3_REGION_NAME", "auto"),
)

uploaded_files = 0
uploaded_bytes = 0

for source in sorted(path for path in media_root.rglob("*") if path.is_file()):
    key = source.relative_to(media_root).as_posix()
    content_type, _ = mimetypes.guess_type(source.name)
    extra_args = {"ContentType": content_type} if content_type else None
    if extra_args:
        client.upload_file(str(source), bucket, key, ExtraArgs=extra_args)
    else:
        client.upload_file(str(source), bucket, key)
    uploaded_files += 1
    uploaded_bytes += source.stat().st_size

print(f"Uploaded {uploaded_files} files ({uploaded_bytes} bytes) to {bucket}.")

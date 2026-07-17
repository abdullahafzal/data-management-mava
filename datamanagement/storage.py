"""Media storage: local disk (for .path) with optional S3 mirror."""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage

logger = logging.getLogger(__name__)


class LocalAndS3Storage(FileSystemStorage):
    """
    Primary storage stays on local MEDIA_ROOT so existing code that uses
    FileField.path / pandas / merge tools keeps working.

    When USE_S3 is enabled, every saved file is also uploaded to the S3 bucket.
    Missing local files are pulled from S3 on demand (e.g. after a fresh deploy).
    """

    def _s3_enabled(self) -> bool:
        return bool(getattr(settings, 'USE_S3', False))

    def _local_path(self, name: str) -> Path:
        """Filesystem path without triggering S3 download (avoids recursion)."""
        return Path(super().path(name))

    def _s3_client(self):
        import boto3

        kwargs = {'region_name': settings.AWS_S3_REGION_NAME}
        key = getattr(settings, 'AWS_ACCESS_KEY_ID', '') or ''
        secret = getattr(settings, 'AWS_SECRET_ACCESS_KEY', '') or ''
        if key and secret:
            kwargs['aws_access_key_id'] = key
            kwargs['aws_secret_access_key'] = secret
        return boto3.client('s3', **kwargs)

    def _s3_key(self, name: str) -> str:
        prefix = (getattr(settings, 'AWS_LOCATION', '') or 'media').strip('/')
        name = name.lstrip('/')
        return f'{prefix}/{name}' if prefix else name

    def _upload_to_s3(self, name: str) -> None:
        if not self._s3_enabled():
            return
        local = self._local_path(name)
        if not local.is_file():
            return
        try:
            client = self._s3_client()
            client.upload_file(
                str(local),
                settings.AWS_STORAGE_BUCKET_NAME,
                self._s3_key(name),
            )
        except Exception:
            logger.exception('S3 upload failed for %s', name)

    def _download_from_s3(self, name: str) -> bool:
        if not self._s3_enabled():
            return False
        local = self._local_path(name)
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            client = self._s3_client()
            client.download_file(
                settings.AWS_STORAGE_BUCKET_NAME,
                self._s3_key(name),
                str(local),
            )
            return local.is_file()
        except Exception:
            logger.exception('S3 download failed for %s', name)
            return False

    def save(self, name, content, max_length=None):
        name = super().save(name, content, max_length=max_length)
        self._upload_to_s3(name)
        return name

    def delete(self, name):
        super().delete(name)
        if not self._s3_enabled():
            return
        try:
            self._s3_client().delete_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=self._s3_key(name),
            )
        except Exception:
            logger.exception('S3 delete failed for %s', name)

    def path(self, name):
        local = self._local_path(name)
        if not local.is_file() and self._s3_enabled() and name:
            self._download_from_s3(name)
        return str(local)

    def exists(self, name):
        if super().exists(name):
            return True
        if not self._s3_enabled() or not name:
            return False
        try:
            self._s3_client().head_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=self._s3_key(name),
            )
            return True
        except Exception:
            return False

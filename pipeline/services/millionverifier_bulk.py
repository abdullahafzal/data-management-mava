from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Any

import requests


class MillionVerifierBulkError(Exception):
    pass


BULK_BASE_URL = "https://bulkapi.millionverifier.com"


@dataclass(frozen=True)
class BulkUploadResult:
    file_id: str
    raw: dict[str, Any]


def upload_csv(api_key: str, csv_bytes: bytes, *, filename: str = "emails.csv") -> BulkUploadResult:
    if not api_key or not api_key.strip():
        raise MillionVerifierBulkError("MillionVerifier API key is missing.")

    url = f"{BULK_BASE_URL}/bulkapi/v2/upload"
    files = {
        "file_contents": (filename, io.BytesIO(csv_bytes), "text/csv"),
    }
    try:
        r = requests.post(url, params={"key": api_key.strip()}, files=files, timeout=60)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise MillionVerifierBulkError(str(exc)) from exc
    except ValueError as exc:
        raise MillionVerifierBulkError(f"Invalid JSON response from MillionVerifier: {exc}") from exc

    file_id = str(data.get("file_id") or data.get("fileId") or data.get("id") or "").strip()
    if not file_id:
        raise MillionVerifierBulkError(f"MillionVerifier upload did not return file_id. Response: {data}")
    return BulkUploadResult(file_id=file_id, raw=data)


def file_info(api_key: str, file_id: str) -> dict[str, Any]:
    url = f"{BULK_BASE_URL}/bulkapi/v2/fileinfo"
    try:
        r = requests.get(url, params={"key": api_key.strip(), "file_id": file_id}, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise MillionVerifierBulkError(str(exc)) from exc
    except ValueError as exc:
        raise MillionVerifierBulkError(f"Invalid JSON response from MillionVerifier: {exc}") from exc


def wait_until_done(
    api_key: str,
    file_id: str,
    *,
    timeout_seconds: int = 120,
    poll_every_seconds: float = 3.0,
) -> dict[str, Any]:
    start = time.time()
    last = None
    while time.time() - start < timeout_seconds:
        last = file_info(api_key, file_id)
        status = str(last.get("status") or last.get("file_status") or "").lower()
        # Seen in the wild: "completed", "done"
        if status in {"completed", "complete", "done", "finished"}:
            return last
        if status in {"failed", "error"}:
            raise MillionVerifierBulkError(f"MillionVerifier bulk job failed. Info: {last}")
        time.sleep(poll_every_seconds)
    raise MillionVerifierBulkError(f"Timed out waiting for MillionVerifier results. Last info: {last}")


def download_report_csv(api_key: str, file_id: str, *, filter_name: str = "all") -> bytes:
    url = f"{BULK_BASE_URL}/bulkapi/v2/download"
    try:
        r = requests.get(
            url,
            params={"key": api_key.strip(), "file_id": file_id, "filter": filter_name},
            timeout=120,
        )
        r.raise_for_status()
        return r.content
    except requests.RequestException as exc:
        raise MillionVerifierBulkError(str(exc)) from exc


"""GET /jobs — list recent jobs (newest first) for the gallery."""

from __future__ import annotations

import os

from shared import ddb
from shared.response import ok, server_error


def _output_url(job_id: str) -> str | None:
    base = os.environ.get("OUTPUTS_CDN_BASE", "").rstrip("/")
    if not base:
        return None
    return f"{base}/outputs/{job_id}/scene.ply"


def handler(event, _context):
    try:
        items = ddb.list_recent(limit=50)
    except Exception as e:  # noqa: BLE001
        return server_error(str(e))

    jobs = []
    for it in items:
        job = {
            "jobId": it["jobId"],
            "status": it.get("status"),
            "stage": it.get("stage"),
            "preset": it.get("preset"),
            "createdAt": it.get("createdAt"),
        }
        if it.get("status") == "SUCCEEDED" and it.get("outputKey"):
            job["outputUrl"] = _output_url(it["jobId"])
        jobs.append(job)
    return ok({"jobs": jobs})

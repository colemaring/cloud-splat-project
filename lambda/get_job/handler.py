"""GET /jobs/{id} — return a single job's status for the UI to poll."""

from __future__ import annotations

import os

from shared import ddb
from shared.response import not_found, ok


def _output_url(job_id: str) -> str | None:
    base = os.environ.get("OUTPUTS_CDN_BASE", "").rstrip("/")
    if not base:
        return None
    return f"{base}/outputs/{job_id}/scene.ply"


def handler(event, _context):
    job_id = (event.get("pathParameters") or {}).get("id")
    if not job_id:
        return not_found("missing job id")
    item = ddb.get_job(job_id)
    if not item:
        return not_found(f"job {job_id} not found")

    out = {
        "jobId": item["jobId"],
        "status": item.get("status"),
        "stage": item.get("stage"),
        "preset": item.get("preset"),
        "masking": item.get("masking"),
        "stageHistory": item.get("stageHistory", []),
        "error": item.get("error"),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
    }
    if item.get("status") == "SUCCEEDED" and item.get("outputKey"):
        out["outputUrl"] = _output_url(job_id)
    return ok(out)

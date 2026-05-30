"""Pipeline stage model + DynamoDB status updates shared by the worker.

The web UI renders an ordered checklist from ``STAGES``. ``run_job.py`` parses
``##STAGE:<name>`` markers emitted by the pipeline (see
backend/pipeline/pipeline_lichtfeld.emit_stage) and calls :func:`set_stage`,
which writes a single ``UpdateItem`` to the jobs table.

Status model:  QUEUED -> PROVISIONING -> RUNNING -> SUCCEEDED | FAILED
Stage model:   the ordered list below (``masking`` is skipped when the user
               disabled masking; the UI greys it out).
"""

from __future__ import annotations

import time

import boto3

# Ordered pipeline stages. Keep in sync with the frontend status checklist.
STAGES = [
    "provisioning",        # set by the create_job Lambda before the worker boots
    "booting",             # worker process started
    "downloading",         # pulling .insv from S3
    "stitching",           # Insta360 MediaSDK equirect stitch
    "extracting_frames",   # ffmpeg frame sampling
    "masking",             # YOLO person/bottom masking (skipped if masking off)
    "extracting_cubemaps", # ffmpeg v360 cubemap faces
    "running_sfm",         # COLMAP structure-from-motion
    "converting",          # COLMAP -> LichtFeld dataset
    "training",            # LichtFeld-Studio gaussian splat training
    "uploading_result",    # uploading scene.ply to S3
    "done",                # terminal success
    "failed",              # terminal failure
]

# Set of stage names the pipeline can emit as ##STAGE markers (the worker
# ignores anything not in here so a stray log line can't corrupt state).
PIPELINE_STAGES = {
    "stitching", "extracting_frames", "masking", "extracting_cubemaps",
    "running_sfm", "converting", "training",
}

TERMINAL_STAGES = {"done", "failed"}


def _now_ms() -> int:
    return int(time.time() * 1000)


class JobStatus:
    """Thin wrapper over a DynamoDB UpdateItem for one job's status row."""

    def __init__(self, table_name: str, job_id: str, region: str | None = None):
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self.job_id = job_id

    def set_stage(self, stage: str, status: str | None = None, error: str | None = None) -> None:
        """Update the job's current stage (and optionally status/error).

        Appends ``{stage, ts}`` to ``stageHistory`` so the UI can show which
        steps already completed. Always bumps ``updatedAt``.
        """
        ts = _now_ms()
        names = {"#stage": "stage", "#hist": "stageHistory"}
        values = {
            ":stage": stage,
            ":ts": ts,
            ":entry": [{"stage": stage, "ts": ts}],
            ":empty": [],
        }
        set_parts = [
            "#stage = :stage",
            "updatedAt = :ts",
            "#hist = list_append(if_not_exists(#hist, :empty), :entry)",
        ]
        if status is not None:
            names["#status"] = "status"
            values[":status"] = status
            set_parts.append("#status = :status")
            if status == "RUNNING":
                set_parts.append("startedAt = if_not_exists(startedAt, :ts)")
            if status in ("SUCCEEDED", "FAILED"):
                set_parts.append("finishedAt = :ts")
        if error is not None:
            values[":error"] = error[:1500]
            set_parts.append("#err = :error")
            names["#err"] = "error"

        self._table.update_item(
            Key={"jobId": self.job_id},
            UpdateExpression="SET " + ", ".join(set_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def get(self) -> dict:
        resp = self._table.get_item(Key={"jobId": self.job_id})
        item = resp.get("Item")
        if not item:
            raise RuntimeError(f"job {self.job_id} not found in table")
        return item

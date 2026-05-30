"""Scheduled cost-safety backstop (EventBridge, every ~30 min).

Terminates any worker instance tagged Project=cloud-splat that has been running
longer than MAX_INSTANCE_AGE_SECS — covers the case where a worker hangs, never
boots, or fails before its own watchdog/self-shutdown can fire. Also flags the
associated job as FAILED so the UI stops polling.
"""

from __future__ import annotations

import os
import time

import boto3

from shared import ddb

_ec2 = boto3.client("ec2")

# Default 8h: comfortably longer than the worker's own 6h cap + 7h sleep guard,
# so this only fires for genuinely stuck instances.
MAX_AGE = int(os.environ.get("MAX_INSTANCE_AGE_SECS", "28800"))


def _mark_job_failed(job_id: str) -> None:
    item = ddb.get_job(job_id)
    if not item or item.get("status") in ("SUCCEEDED", "FAILED"):
        return
    ddb.table().update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET #s = :s, stage = :st, #e = :e, updatedAt = :t",
        ExpressionAttributeNames={"#s": "status", "#e": "error"},
        ExpressionAttributeValues={
            ":s": "FAILED", ":st": "failed",
            ":e": "instance exceeded max runtime and was terminated by the reaper",
            ":t": ddb.now_ms(),
        },
    )


def handler(_event, _context):
    now = time.time()
    resp = _ec2.describe_instances(
        Filters=[
            {"Name": "tag:Project", "Values": ["cloud-splat"]},
            {"Name": "instance-state-name", "Values": ["pending", "running"]},
        ]
    )
    to_kill = []
    job_ids = []
    for res in resp.get("Reservations", []):
        for inst in res.get("Instances", []):
            launch = inst.get("LaunchTime")
            age = now - launch.timestamp() if launch else 0
            if age < MAX_AGE:
                continue
            to_kill.append(inst["InstanceId"])
            for tag in inst.get("Tags", []):
                if tag["Key"] == "jobId":
                    job_ids.append(tag["Value"])

    if to_kill:
        _ec2.terminate_instances(InstanceIds=to_kill)
        for jid in job_ids:
            try:
                _mark_job_failed(jid)
            except Exception:  # noqa: BLE001
                pass

    return {"terminated": to_kill}

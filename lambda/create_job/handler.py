"""POST /jobs — create a job record and launch its dedicated GPU instance.

Each job gets its own g4dn.xlarge (no shared queue). The worker reads its
job id from EC2 user-data, runs the pipeline, and self-terminates.

Request body: { "frontKey", "backKey", "preset", "masking" }
Response:     { "jobId" }
"""

from __future__ import annotations

import os
import uuid

import boto3

from shared import ddb
from shared.response import bad_request, ok, parse_body, server_error

VALID_PRESETS = {"Draft", "Low", "Medium", "High"}

_ec2 = boto3.client("ec2")


def _user_data(job_id: str) -> str:
    region = os.environ.get("AWS_REGION", "")
    table = os.environ["JOBS_TABLE"]
    uploads = os.environ["UPLOADS_BUCKET"]
    outputs = os.environ["OUTPUTS_BUCKET"]
    max_secs = os.environ.get("MAX_RUNTIME_SECS", "21600")
    guard_secs = os.environ.get("WORKER_SHUTDOWN_GUARD_SECS", "25200")
    # The cost-safety sleep guard fires even if the python worker never starts;
    # it is set longer than MAX_RUNTIME_SECS so the in-worker watchdog wins
    # under normal operation. The reaper Lambda is the final backstop.
    return f"""#!/bin/bash
set -x
( sleep {guard_secs}; shutdown -h now ) &
export JOB_ID="{job_id}"
export JOBS_TABLE="{table}"
export AWS_REGION="{region}"
export UPLOADS_BUCKET="{uploads}"
export OUTPUTS_BUCKET="{outputs}"
export SELF_TERMINATE="1"
export MAX_RUNTIME_SECS="{max_secs}"
cd /opt/worker
/opt/worker/venv/bin/python /opt/worker/run_job.py >> /var/log/cloud-splat-worker.log 2>&1
shutdown -h now
"""


def handler(event, _context):
    body = parse_body(event)
    front_key = body.get("frontKey")
    back_key = body.get("backKey")
    preset = body.get("preset")
    masking = bool(body.get("masking", False))

    if preset not in VALID_PRESETS:
        return bad_request(f"preset must be one of {sorted(VALID_PRESETS)}")
    if not front_key or "_00_" not in front_key:
        return bad_request("frontKey missing or not a _00_ file")
    if not back_key or "_10_" not in back_key:
        return bad_request("backKey missing or not a _10_ file")

    job_id = uuid.uuid4().hex
    now = ddb.now_ms()
    item = {
        "jobId": job_id,
        "status": "QUEUED",
        "stage": "provisioning",
        "preset": preset,
        "masking": masking,
        "frontKey": front_key,
        "backKey": back_key,
        "createdAt": now,
        "updatedAt": now,
        "stageHistory": [{"stage": "provisioning", "ts": now}],
    }
    try:
        ddb.put_job(item)
    except Exception as e:  # noqa: BLE001
        return server_error(f"failed to record job: {e}")

    try:
        resp = _ec2.run_instances(
            LaunchTemplate={
                "LaunchTemplateId": os.environ["LAUNCH_TEMPLATE_ID"],
                "Version": "$Latest",
            },
            MinCount=1,
            MaxCount=1,
            UserData=_user_data(job_id),  # boto3 base64-encodes for run_instances
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Project", "Value": "cloud-splat"},
                        {"Key": "jobId", "Value": job_id},
                        {"Key": "Name", "Value": f"cloud-splat-{job_id[:8]}"},
                    ],
                }
            ],
        )
        instance_id = resp["Instances"][0]["InstanceId"]
    except Exception as e:  # noqa: BLE001
        # Mark the job failed so the UI doesn't poll forever.
        ddb.table().update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, stage = :st, #e = :e, updatedAt = :t",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={
                ":s": "FAILED", ":st": "failed",
                ":e": f"failed to launch GPU instance: {e}", ":t": ddb.now_ms(),
            },
        )
        return server_error(f"failed to launch GPU instance: {e}")

    ddb.table().update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET #s = :s, instanceId = :i, updatedAt = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "PROVISIONING", ":i": instance_id, ":t": ddb.now_ms()},
    )
    return ok({"jobId": job_id, "instanceId": instance_id})

"""DynamoDB access helpers for the jobs table.

Table schema (see terraform/modules/data):
  PK  jobId (S)
  GSI byCreated:  PK gsiPk = "JOB" (constant), SK createdAt (N, epoch ms)

A job item:
  jobId, status, stage, preset, masking, frontKey, backKey, outputKey,
  createdAt, updatedAt, startedAt, finishedAt, instanceId, error,
  stageHistory[], gsiPk
"""

from __future__ import annotations

import os
import time

import boto3
from boto3.dynamodb.conditions import Key

GSI_NAME = "byCreated"
GSI_PK_VALUE = "JOB"

_TABLE = None


def table():
    global _TABLE
    if _TABLE is None:
        name = os.environ["JOBS_TABLE"]
        _TABLE = boto3.resource("dynamodb").Table(name)
    return _TABLE


def now_ms() -> int:
    return int(time.time() * 1000)


def put_job(item: dict) -> None:
    item.setdefault("gsiPk", GSI_PK_VALUE)
    table().put_item(Item=item)


def get_job(job_id: str) -> dict | None:
    return table().get_item(Key={"jobId": job_id}).get("Item")


def list_recent(limit: int = 50) -> list[dict]:
    resp = table().query(
        IndexName=GSI_NAME,
        KeyConditionExpression=Key("gsiPk").eq(GSI_PK_VALUE),
        ScanIndexForward=False,  # newest first
        Limit=limit,
    )
    return resp.get("Items", [])

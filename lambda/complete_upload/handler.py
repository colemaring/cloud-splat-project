"""POST /uploads/complete — finalize the multipart uploads.

The browser PUTs each part directly to S3, collects the ETag from each part's
response, and posts them here to assemble the final objects.

Request body:
  { "files": [ {"key","uploadId","parts":[{"partNumber","etag"}, ...]}, ... ] }

Response: { "ok": true }
"""

from __future__ import annotations

import os

import boto3

from shared.response import bad_request, ok, parse_body, server_error

_s3 = boto3.client("s3")


def handler(event, _context):
    bucket = os.environ["UPLOADS_BUCKET"]
    body = parse_body(event)
    files = body.get("files")
    if not isinstance(files, list) or not files:
        return bad_request("expected a non-empty 'files' list")

    try:
        for f in files:
            key = f.get("key")
            upload_id = f.get("uploadId")
            parts = f.get("parts")
            if not key or not upload_id or not isinstance(parts, list) or not parts:
                return bad_request("each file needs key, uploadId, and parts")
            mp_parts = sorted(
                ({"PartNumber": int(p["partNumber"]), "ETag": p["etag"]} for p in parts),
                key=lambda p: p["PartNumber"],
            )
            _s3.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": mp_parts},
            )
        return ok({"ok": True})
    except Exception as e:  # noqa: BLE001
        return server_error(str(e))

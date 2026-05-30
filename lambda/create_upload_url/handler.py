"""POST /uploads — start multipart uploads for the front+back .insv files and
return presigned UploadPart URLs so the browser uploads straight to S3.

Request body:
  { "files": [ {"role": "front", "name": "VID_..._00_...insv", "parts": 12},
               {"role": "back",  "name": "VID_..._10_...insv", "parts": 12} ] }

Response:
  { "uploadPrefix": "...",
    "files": [ {"role","key","uploadId","parts":[{"partNumber","url"}, ...]} ] }
"""

from __future__ import annotations

import os
import uuid

import boto3

from shared.response import bad_request, ok, parse_body, server_error

PART_URL_EXPIRY = 6 * 3600  # 6h — multi-GB uploads on slow links
MAX_PARTS = 10000           # S3 multipart hard limit

_s3 = boto3.client("s3")

# role -> required substring in the filename (Insta360 dual-fisheye convention)
ROLE_MARKERS = {"front": "_00_", "back": "_10_"}


def handler(event, _context):
    bucket = os.environ["UPLOADS_BUCKET"]
    body = parse_body(event)
    files = body.get("files")
    if not isinstance(files, list) or len(files) != 2:
        return bad_request("expected exactly two files (front and back)")

    roles = {f.get("role") for f in files}
    if roles != {"front", "back"}:
        return bad_request("files must have roles 'front' and 'back'")

    prefix = uuid.uuid4().hex
    try:
        out_files = []
        for f in files:
            role = f["role"]
            name = (f.get("name") or "").strip()
            parts = int(f.get("parts", 0))
            if not name:
                return bad_request(f"{role}: missing filename")
            marker = ROLE_MARKERS[role]
            if marker not in name:
                return bad_request(f"{role} file must contain '{marker}' (got {name!r})")
            if "_11_" in name:
                return bad_request("the _11_ (LRV) file should not be uploaded")
            if parts < 1 or parts > MAX_PARTS:
                return bad_request(f"{role}: parts must be between 1 and {MAX_PARTS}")

            safe_name = os.path.basename(name)
            key = f"uploads/{prefix}/{role}/{safe_name}"
            mpu = _s3.create_multipart_upload(
                Bucket=bucket, Key=key, ContentType="application/octet-stream"
            )
            upload_id = mpu["UploadId"]
            part_urls = [
                {
                    "partNumber": n,
                    "url": _s3.generate_presigned_url(
                        "upload_part",
                        Params={
                            "Bucket": bucket, "Key": key,
                            "UploadId": upload_id, "PartNumber": n,
                        },
                        ExpiresIn=PART_URL_EXPIRY,
                    ),
                }
                for n in range(1, parts + 1)
            ]
            out_files.append(
                {"role": role, "key": key, "uploadId": upload_id, "parts": part_urls}
            )
        return ok({"uploadPrefix": prefix, "files": out_files})
    except Exception as e:  # noqa: BLE001
        return server_error(str(e))

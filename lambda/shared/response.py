"""HTTP response helpers for the API Gateway (HTTP API) Lambda integrations."""

from __future__ import annotations

import decimal
import json
from typing import Any

# CORS is also configured on the HTTP API itself, but echoing permissive
# headers here keeps direct invocations and preflight-less GETs working.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json",
}


class _DecimalEncoder(json.JSONEncoder):
    """DynamoDB returns numbers as Decimal; render them as int/float in JSON."""

    def default(self, o: Any):
        if isinstance(o, decimal.Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def respond(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, cls=_DecimalEncoder),
    }


def ok(body: Any) -> dict:
    return respond(200, body)


def bad_request(msg: str) -> dict:
    return respond(400, {"error": msg})


def not_found(msg: str = "not found") -> dict:
    return respond(404, {"error": msg})


def server_error(msg: str) -> dict:
    return respond(500, {"error": msg})


def parse_body(event: dict) -> dict:
    """Return the JSON body of an HTTP API (payload v2) event as a dict."""
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}

# SPDX-License-Identifier: MPL-2.0
"""Privacy-minimized API observation contract for the Guard Connector SDK."""

import json
import math
import re
from datetime import datetime, timezone
from uuid import UUID


MAX_BODY_BYTES = 256 * 1024
MAX_EVENTS = 500
MAX_ROUTE_CHARS = 2048
MAX_LATENCY_MS = 2**31 - 1
MAX_TRANSFER_BYTES = 2**63 - 1

_REQUIRED_FIELDS = frozenset({"timestamp", "api_route", "http_method", "status_code"})
_MEASUREMENTS = {
    "latency_ms": MAX_LATENCY_MS,
    "request_bytes": MAX_TRANSFER_BYTES,
    "response_bytes": MAX_TRANSFER_BYTES,
}
_ALLOWED_FIELDS = _REQUIRED_FIELDS | _MEASUREMENTS.keys() | {"request_id"}
_TIMESTAMP = re.compile(
    r"(?P<date_time>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,6}))?(?P<offset>Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
    re.ASCII,
)
_METHOD = re.compile(r"[A-Za-z]{1,16}", re.ASCII)
_ROUTE = re.compile(r"/[A-Za-z0-9/._~{}-]*", re.ASCII)
_UUID = re.compile(
    r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", re.ASCII
)


class InputError(ValueError):
    """A validation error with a message that contains no observed data."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise InputError("JSON numbers must be finite")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _reject_constant(value)
    return number


def decode_json(data: bytes) -> object:
    """Decode bounded UTF-8 JSON without accepting duplicate or nonfinite values."""
    if len(data) > MAX_BODY_BYTES:
        raise InputError("JSON exceeds the 256 KiB byte limit")
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except InputError:
        raise
    except (ValueError, RecursionError):
        raise InputError("invalid UTF-8 JSON") from None


def _timestamp(value: object) -> str:
    match = _TIMESTAMP.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise InputError("timestamp must be RFC 3339 with an explicit offset")
    iso_date_time = match["date_time"]
    if fraction := match["fraction"]:
        iso_date_time += "." + fraction.ljust(6, "0")
    offset = "+00:00" if match["offset"] == "Z" else match["offset"]
    try:
        parsed = datetime.fromisoformat(iso_date_time + offset)
        return parsed.astimezone(timezone.utc).isoformat().removesuffix("+00:00") + "Z"
    except (ValueError, OverflowError):
        raise InputError("timestamp is outside the supported date range") from None


def _route(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_ROUTE_CHARS:
        raise InputError("api_route must be a bounded relative path")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise InputError("api_route contains control characters")
    path = value.partition("?")[0].partition("#")[0]
    if not _ROUTE.fullmatch(path) or "//" in path:
        raise InputError("api_route contains an unsupported path component")
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise InputError("api_route must exclude dot path segments")
    return path


def _bounded_int(value: object, minimum: int, maximum: int, field: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InputError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def validate_event(value: object) -> dict:
    """Return a new normalized event with only the allowed metadata fields."""
    if not isinstance(value, dict):
        raise InputError("each event must be a JSON object")
    if not _REQUIRED_FIELDS <= value.keys():
        raise InputError("event is missing required API observation fields")
    if not value.keys() <= _ALLOWED_FIELDS:
        raise InputError("event contains fields outside the API observation schema")

    method = value["http_method"]
    if not isinstance(method, str) or not _METHOD.fullmatch(method):
        raise InputError("http_method must contain 1 to 16 ASCII letters")
    result = {
        "timestamp": _timestamp(value["timestamp"]),
        "api_route": _route(value["api_route"]),
        "http_method": method.upper(),
        "status_code": _bounded_int(value["status_code"], 100, 599, "status_code"),
    }
    if "request_id" in value:
        request_id = value["request_id"]
        if not isinstance(request_id, str) or not _UUID.fullmatch(request_id):
            raise InputError("request_id must be a hyphenated UUID")
        result["request_id"] = str(UUID(request_id))
    for field, maximum in _MEASUREMENTS.items():
        if field in value:
            result[field] = _bounded_int(value[field], 0, maximum, field)
    return result


def _normalize_request(value: object) -> list[dict]:
    if not isinstance(value, dict) or value.keys() != {"events"}:
        raise InputError("request must contain only the events field")
    events = value["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise InputError("request must contain 1 to 500 events")
    result = []
    for number, event in enumerate(events, start=1):
        try:
            result.append(validate_event(event))
        except InputError as error:
            raise InputError(f"event {number}: {error}") from None
    return result


def _encode_normalized(events: list[dict]) -> bytes:
    data = json.dumps({"events": events}, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(data) > MAX_BODY_BYTES:
        raise InputError("normalized request exceeds the 256 KiB byte limit")
    return data


def validate_request(value: object) -> list[dict]:
    """Validate the envelope, including the byte size after normalization."""
    events = _normalize_request(value)
    _encode_normalized(events)
    return events


def encode_request(events: list[dict]) -> bytes:
    """Encode one bounded batch in Guard's API ingestion envelope."""
    return _encode_normalized(_normalize_request({"events": events}))

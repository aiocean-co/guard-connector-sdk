# SPDX-License-Identifier: MPL-2.0
"""Send one bounded API telemetry batch and verify its acknowledgement."""

from dataclasses import dataclass
from http.client import HTTPException
import ipaddress
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import UUID, uuid4

from .schema import InputError, decode_json, encode_request

INGEST_PATH = "/v1/ingest/events"
API_KEY_ENV = "GUARD_CONNECTOR_API_KEY"
MAX_RECEIPT_BYTES = 4096
EXPLICIT_REJECTIONS = {400, 401, 403, 404, 405, 413, 415, 422}


@dataclass(frozen=True)
class Receipt:
    request_id: str
    organization_id: str
    accepted_events: int
    advanced_analysis_enqueued: bool


class DeliveryError(Exception):
    """Public, content-free error describing the outcome of a POST."""

    def __init__(self, reason, request_id, *, rejected=False, http_status=None):
        super().__init__(reason)
        self.reason = reason
        self.request_id = request_id
        self.outcome = "rejected" if rejected else "unknown"
        self.http_status = http_status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _checked_endpoint(endpoint, allow_loopback_http):
    if (not isinstance(endpoint, str) or not endpoint.isascii()
            or any(ord(character) <= 32 or ord(character) == 127 for character in endpoint)
            or any(delimiter in endpoint for delimiter in ("?", "#", "\\"))):
        raise InputError("invalid intake URL")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        host = parsed.hostname
    except ValueError:
        raise InputError("invalid intake URL") from None
    if (not host or parsed.username is not None or parsed.password is not None
            or parsed.path != INGEST_PATH or port == 0
            or re.fullmatch(r"(?:\[[0-9a-fA-F:]+\]|[a-zA-Z0-9.\-]+)(?::[0-9]+)?",
                            parsed.netloc) is None):
        raise InputError("intake URL must end with /v1/ingest/events on a trusted origin")
    if parsed.scheme == "https":
        return endpoint
    if parsed.scheme == "http" and allow_loopback_http:
        try:
            if ipaddress.ip_address(host) in (ipaddress.ip_address("127.0.0.1"),
                                              ipaddress.ip_address("::1")):
                return endpoint
        except ValueError:
            pass
    raise InputError("intake requires HTTPS or explicitly enabled numeric loopback HTTP")


def _expected_organization(value):
    try:
        normalized = UUID(value)
        if normalized.int and str(normalized) == value:
            return value
    except (AttributeError, ValueError, TypeError):
        pass
    raise InputError("organization ID must be a canonical UUID")


class GuardClient:
    """Client for the scoped API telemetry intake.

    Each call sends one POST. A request ID supports tracing instead of
    authorizing retries. Verify an unknown outcome before resending.
    """

    def __init__(self, endpoint: str, *, organization_id: str, api_key: str,
                 allow_loopback_http: bool = False):
        self._endpoint = _checked_endpoint(endpoint, allow_loopback_http)
        self._organization_id = _expected_organization(organization_id)
        if not isinstance(api_key, str) or re.fullmatch(
            r"[A-Za-z0-9._~+/\-]{1,510}={0,2}", api_key,
        ) is None:
            raise InputError("missing or invalid intake credential")
        self._api_key = api_key
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def send(self, events: list[dict]) -> Receipt:
        body = encode_request(events)
        trace = str(uuid4())
        request = Request(self._endpoint, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + self._api_key,
            "x-request-id": trace,
        })
        try:
            with self._opener.open(request, timeout=10) as response:
                if (response.status != 200
                        or response.headers.get_content_type() != "application/json"
                        or response.headers.get_all("x-request-id") != [trace]):
                    raise DeliveryError("unverified_acknowledgement", trace)
                data = response.read(MAX_RECEIPT_BYTES + 1)
        except HTTPError as error:
            error.close()
            rejected = error.code in EXPLICIT_REJECTIONS
            reason = "http_rejected" if rejected else "http_unconfirmed"
            raise DeliveryError(reason, trace, rejected=rejected,
                                http_status=error.code) from None
        except (OSError, HTTPException):
            raise DeliveryError("transport_unconfirmed", trace) from None
        if len(data) > MAX_RECEIPT_BYTES:
            raise DeliveryError("unverified_acknowledgement", trace)
        try:
            ack = decode_json(data)
        except InputError:
            raise DeliveryError("unverified_acknowledgement", trace) from None
        if (not isinstance(ack, dict) or ack.get("organization_id") != self._organization_id
                or type(ack.get("accepted_events")) is not int
                or ack["accepted_events"] != len(events)
                or type(ack.get("advanced_analysis_enqueued")) is not bool):
            raise DeliveryError("unverified_acknowledgement", trace)
        return Receipt(trace, self._organization_id, ack["accepted_events"],
                       ack["advanced_analysis_enqueued"])

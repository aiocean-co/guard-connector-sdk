# SPDX-License-Identifier: MPL-2.0
"""Loopback-only HTTP receiver for the Guard Connector SDK quickstart."""

import argparse
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import re
import sys
from uuid import UUID, uuid4

from .schema import MAX_BODY_BYTES, InputError, decode_json, validate_request


LOOPBACK_HOST = "127.0.0.1"
TEST_ORGANIZATION = "00000000-0000-4000-8000-000000000001"
TOKEN_ENV = "GUARD_CONNECTOR_TEST_TOKEN"
INGEST_PATH = "/v1/ingest/events"
SOCKET_TIMEOUT_SECONDS = 5
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/\-]{1,510}={0,2}")


def _canonical_uuid(value):
    try:
        return value == str(UUID(value))
    except (AttributeError, TypeError, ValueError):
        return False


class _Receiver(HTTPServer):
    def __init__(self, host, port, token, organization_id):
        self._token = token
        self.organization_id = organization_id
        super().__init__((host, port), _Handler)

    def get_request(self):
        client, address = super().get_request()
        client.settimeout(SOCKET_TIMEOUT_SECONDS)
        return client, address

    def authorized(self, presented):
        return (
            presented is not None
            and presented.startswith("Bearer ")
            and TOKEN_PATTERN.fullmatch(presented[7:]) is not None
            and hmac.compare_digest(presented[7:], self._token)
        )

    def handle_error(self, request, client_address):
        # A disconnect or a malformed request should never dump request data.
        pass


class _Handler(BaseHTTPRequestHandler):
    # Keep one request per connection. Rejected request bodies are discarded
    # by closing the socket, rather than parsing them as another request.
    protocol_version = "HTTP/1.0"
    server_version = "GuardConnectorTestReceiver"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    def _single_header(self, name):
        values = self.headers.get_all(name, []) if hasattr(self, "headers") else []
        return values[0] if len(values) == 1 else None

    def _trace(self):
        candidate = self._single_header("x-request-id")
        return candidate if _canonical_uuid(candidate) else None

    def _matches_path(self, path):
        # BaseHTTPRequestHandler collapses leading double slashes in self.path.
        # Compare the original request target so aliases do not satisfy checks.
        return self.requestline.split()[1] == path

    def _reply(self, status, payload, trace=None):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("x-request-id", trace or str(uuid4()))
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        # The standard handler may include raw request text in error details.
        self._reply(code, {"error": "http_request_rejected"}, self._trace())

    def do_GET(self):
        if self._matches_path("/readyz"):
            self._reply(HTTPStatus.OK, {"status": "ready"}, self._trace())
            return
        self._reply(HTTPStatus.NOT_FOUND, {"error": "route_not_found"}, self._trace())

    def do_POST(self):
        trace = self._trace()
        if not self._matches_path(INGEST_PATH):
            self._reply(HTTPStatus.NOT_FOUND, {"error": "route_not_found"}, trace)
            return
        if not self.server.authorized(self._single_header("Authorization")):
            self._reply(HTTPStatus.UNAUTHORIZED, {"error": "authentication_required"}, trace)
            return
        if trace is None:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": "invalid_request_id"})
            return
        content_type = self._single_header("Content-Type")
        if content_type is None or content_type.split(";", 1)[0].strip().lower() != "application/json":
            self._reply(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "json_required"}, trace)
            return
        length = self._single_header("Content-Length")
        if (
            self.headers.get_all("Transfer-Encoding")
            or length is None
            or re.fullmatch(r"[0-9]{1,10}", length) is None
            or int(length) == 0
        ):
            self._reply(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"}, trace)
            return
        size = int(length)
        if size > MAX_BODY_BYTES:
            self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large"}, trace)
            return
        try:
            body = self.rfile.read(size)
            if len(body) != size:
                raise InputError("Incomplete request body")
            events = validate_request(decode_json(body))
        except (InputError, TimeoutError):
            self._reply(HTTPStatus.BAD_REQUEST, {"error": "invalid_events"}, trace)
            return
        self._reply(
            HTTPStatus.OK,
            {
                "organization_id": self.server.organization_id,
                "accepted_events": len(events),
                "advanced_analysis_enqueued": False,
            },
            trace,
        )


def create_receiver(host, port, token, organization_id):
    """Bind an in-memory receiver to the local IPv4 loopback interface."""
    if host != LOOPBACK_HOST:
        raise ValueError("The test receiver binds only to 127.0.0.1")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("The port must be between 0 and 65535")
    if not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None:
        raise ValueError(f"Set {TOKEN_ENV} to a nonempty bearer token")
    if not _canonical_uuid(organization_id) or not UUID(organization_id).int:
        raise ValueError("The organization ID must be a non-nil canonical UUID")
    return _Receiver(host, port, token, organization_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=LOOPBACK_HOST)
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--organization-id", default=TEST_ORGANIZATION)
    args = parser.parse_args(argv)
    try:
        server = create_receiver(args.host, args.port, os.environ.get(TOKEN_ENV), args.organization_id)
    except ValueError as error:
        parser.error(str(error))
    except OSError:
        print("Could not bind the local receiver", file=sys.stderr)
        return 1
    with server:
        print(f"Guard Connector test receiver at http://{LOOPBACK_HOST}:{server.server_port}",
              flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

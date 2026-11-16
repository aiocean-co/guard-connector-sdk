# SPDX-License-Identifier: MPL-2.0
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from threading import Thread
import unittest
from unittest.mock import patch
from uuid import UUID

from guard_connector_sdk.schema import InputError
from guard_connector_sdk.transport import DeliveryError, GuardClient, INGEST_PATH


ORGANIZATION = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
TOKEN = "fixture-token"
EVENT = {"timestamp": "2026-01-01T00:00:00Z", "api_route": "/health",
         "http_method": "GET", "status_code": 200}


@contextmanager
def server(*, status=200, trace=True, changes=None, body=None, disconnect=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append((self.path, self.headers, json.loads(
                self.rfile.read(int(self.headers["Content-Length"])))))
            if disconnect:
                self.close_connection = True
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if status in (301, 302, 307, 308):
                self.send_header("Location", "http://127.0.0.1:%d%s" %
                                 (self.server.server_port, INGEST_PATH))
            if trace:
                self.send_header("x-request-id", self.headers["x-request-id"])
            self.end_headers()
            ack = {"organization_id": ORGANIZATION, "accepted_events": 1,
                   "advanced_analysis_enqueued": False}
            ack.update(changes or {})
            self.wfile.write(body if body is not None else json.dumps(ack).encode("ascii"))

    with HTTPServer(("127.0.0.1", 0), Handler) as http:
        worker = Thread(target=http.serve_forever, kwargs={"poll_interval": 0.05})
        worker.start()
        try:
            yield "http://127.0.0.1:%d%s" % (http.server_port, INGEST_PATH), requests
        finally:
            http.shutdown()
            worker.join(timeout=2)


def connector(endpoint, *, token=TOKEN, organization_id=ORGANIZATION):
    return GuardClient(endpoint, api_key=token,
                       organization_id=organization_id, allow_loopback_http=True)


class TransportTests(unittest.TestCase):
    def test_one_post_strips_query_and_checks_acknowledgement(self):
        with server() as (endpoint, requests):
            with patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:1",
                                          "no_proxy": ""}):
                receipt = connector(endpoint).send([EVENT | {"api_route": "/health?secret=private"}])
            self.assertEqual(len(requests), 1)
        path, headers, document = requests[0]
        self.assertEqual(path, INGEST_PATH)
        self.assertEqual(headers["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(document["events"][0]["api_route"], "/health")
        self.assertEqual(str(UUID(receipt.request_id)), headers["x-request-id"])
        self.assertEqual(receipt.accepted_events, 1)

    def test_refuses_untrusted_endpoint_shapes_before_sending(self):
        endpoints = (
            "http://example.invalid/v1/ingest/events",
            "http://localhost/v1/ingest/events",
            "http://127.1/v1/ingest/events",
            "https://operator:secret@example.invalid/v1/ingest/events",
            "https://example.invalid/v1/ingest/events?secret=value",
            "https://example.invalid/v1/proxy",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint), self.assertRaises(InputError):
                connector(endpoint)
        with self.assertRaises(InputError):
            GuardClient("http://127.0.0.1/v1/ingest/events",
                        api_key=TOKEN, organization_id=ORGANIZATION)
        with self.assertRaises(InputError):
            connector("https://example.invalid/v1/ingest/events", token="bad\r\nBearer bad")

    def test_failed_preflight_sends_nothing(self):
        with server() as (endpoint, requests):
            with self.assertRaises(InputError):
                connector(endpoint).send([EVENT, EVENT | {"body": "private"}])
            self.assertEqual(requests, [])

    def test_refuses_redirect_without_repeating_post(self):
        for status in (302, 307, 308):
            with self.subTest(status=status), server(status=status) as (endpoint, requests):
                with self.assertRaises(DeliveryError) as failure:
                    connector(endpoint).send([EVENT])
                self.assertEqual(failure.exception.outcome, "unknown")
                self.assertEqual(len(requests), 1)

    def test_server_error_and_connection_drop_are_not_retried(self):
        for options in ({"status": 503}, {"status": 429}, {"disconnect": True}):
            with self.subTest(options=options), server(**options) as (endpoint, requests):
                with self.assertRaises(DeliveryError) as failure:
                    connector(endpoint).send([EVENT])
                self.assertEqual(failure.exception.outcome, "unknown")
                self.assertEqual(len(requests), 1)

    def test_rejected_credentials_do_not_echo_error_body(self):
        with server(status=401, body=b'{"secret":"private-message"}') as (endpoint, requests):
            with self.assertRaises(DeliveryError) as failure:
                connector(endpoint).send([EVENT])
            self.assertEqual(failure.exception.outcome, "rejected")
            self.assertEqual(failure.exception.http_status, 401)
            self.assertNotIn("private-message", str(failure.exception))
            self.assertEqual(len(requests), 1)

    def test_ambiguous_acknowledgements_are_unknown(self):
        cases = ({"changes": {"organization_id": OTHER}},
                 {"changes": {"accepted_events": True}},
                 {"changes": {"accepted_events": 0}},
                 {"changes": {"advanced_analysis_enqueued": None}},
                 {"trace": False}, {"body": b"["}, {"body": b"x" * 4097})
        for options in cases:
            with self.subTest(options=options), server(**options) as (endpoint, requests):
                with self.assertRaises(DeliveryError) as failure:
                    connector(endpoint).send([EVENT])
                self.assertEqual(failure.exception.outcome, "unknown")
                self.assertEqual(len(requests), 1)

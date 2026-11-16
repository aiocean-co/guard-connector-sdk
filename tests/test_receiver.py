# SPDX-License-Identifier: MPL-2.0

from http.client import HTTPConnection
import json
from threading import Thread
import unittest
from uuid import uuid4

from guard_connector_sdk.receiver import (
    INGEST_PATH,
    LOOPBACK_HOST,
    TEST_ORGANIZATION,
    create_receiver,
)
from guard_connector_sdk.schema import MAX_BODY_BYTES


TOKEN = "guard-connector-test-only"
EVENT = {
    "timestamp": "2026-01-19T12:34:56Z",
    "request_id": "00000000-0000-4000-8000-000000000099",
    "api_route": "/demo/items",
    "http_method": "GET",
    "status_code": 200,
}
BODY = json.dumps({"events": [EVENT]}).encode("utf-8")


class ReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_receiver(LOOPBACK_HOST, 0, TOKEN, TEST_ORGANIZATION)
        cls.thread = Thread(
            target=cls.server.serve_forever, kwargs={"poll_interval": 0.02}
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=3)
        cls.server.server_close()

    def _request(self, verb, path, body=None, extra_headers=None):
        connection = HTTPConnection(LOOPBACK_HOST, self.server.server_port, timeout=3)
        try:
            connection.request(verb, path, body, extra_headers or {})
            with connection.getresponse() as response:
                return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def _headers(self):
        return {
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
            "x-request-id": str(uuid4()),
        }

    def test_http_health_and_actual_acceptance_envelope(self):
        headers = self._headers()
        status, ready_headers, readiness = self._request("GET", "/readyz", extra_headers=headers)
        self.assertEqual((status, readiness), (200, {"status": "ready"}))
        self.assertEqual(ready_headers["x-request-id"], headers["x-request-id"])
        status, accepted_headers, reply = self._request("POST", INGEST_PATH, BODY, headers)
        self.assertEqual(status, 200)
        self.assertEqual(accepted_headers["x-request-id"], headers["x-request-id"])
        self.assertEqual(accepted_headers["Cache-Control"], "no-store")
        self.assertEqual(reply, {
            "organization_id": TEST_ORGANIZATION,
            "accepted_events": 1,
            "advanced_analysis_enqueued": False,
        })

    def test_wrong_bearer_is_rejected_without_reflection(self):
        headers = self._headers()
        headers["Authorization"] = "Bearer incorrect-test-token"
        status, response_headers, reply = self._request("POST", INGEST_PATH, BODY, headers)
        self.assertEqual(status, 401)
        self.assertEqual(response_headers["x-request-id"], headers["x-request-id"])
        self.assertEqual(reply, {"error": "authentication_required"})

    def test_rejects_malformed_and_noncanonical_paths_and_batches(self):
        for path in (INGEST_PATH + "?alias=1", "/" + INGEST_PATH):
            with self.subTest(path=path):
                result = self._request("POST", path, BODY, self._headers())
                self.assertEqual(result[0], 404)
        for body in [
            b'{"events":[],"events":[]}',
            b'{"events":NaN}',
            json.dumps({"events": [EVENT | {"payload": "privacy-canary"}]}).encode("utf-8"),
            json.dumps({"events": [EVENT], "organization_id": TEST_ORGANIZATION}).encode("utf-8"),
        ]:
            with self.subTest(body=body):
                status, _, reply = self._request("POST", INGEST_PATH, body, self._headers())
                self.assertEqual((status, reply), (400, {"error": "invalid_events"}))

    def test_rejects_oversized_body_before_reading(self):
        headers = self._headers() | {"Content-Length": str(MAX_BODY_BYTES + 1)}
        status, _, reply = self._request("POST", INGEST_PATH, b"", headers)
        self.assertEqual((status, reply), (413, {"error": "body_too_large"}))

    def test_missing_or_invalid_request_id_cannot_be_echoed(self):
        for trace in (None, "not-a-request-id"):
            with self.subTest(trace=trace):
                headers = self._headers()
                if trace is None:
                    del headers["x-request-id"]
                else:
                    headers["x-request-id"] = trace
                status, response_headers, reply = self._request("POST", INGEST_PATH, BODY, headers)
                self.assertEqual((status, reply), (400, {"error": "invalid_request_id"}))
                self.assertNotEqual(response_headers["x-request-id"], trace)

    def test_duplicate_length_is_rejected_before_parsing_body(self):
        connection = HTTPConnection(LOOPBACK_HOST, self.server.server_port, timeout=3)
        try:
            connection.putrequest("POST", INGEST_PATH)
            for key, value in self._headers().items():
                connection.putheader(key, value)
            connection.putheader("Content-Length", "1")
            connection.putheader("Content-Length", "2")
            connection.endheaders()
            with connection.getresponse() as response:
                self.assertEqual(response.status, 400)
                self.assertEqual(json.loads(response.read()), {"error": "invalid_content_length"})
        finally:
            connection.close()

    def test_creation_requires_loopback_token_and_canonical_scope(self):
        for host, token, scope in [
            ("localhost", TOKEN, TEST_ORGANIZATION),
            ("0.0.0.0", TOKEN, TEST_ORGANIZATION),
            (LOOPBACK_HOST, "", TEST_ORGANIZATION),
            (LOOPBACK_HOST, TOKEN, "invalid-organization"),
            (LOOPBACK_HOST, TOKEN, "00000000-0000-0000-0000-000000000000"),
        ]:
            with self.subTest(host=host, scope=scope):
                with self.assertRaises(ValueError):
                    create_receiver(host, 0, token, scope)


if __name__ == "__main__":
    unittest.main()

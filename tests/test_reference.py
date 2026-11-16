# SPDX-License-Identifier: MPL-2.0

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from guard_connector_sdk.reference import read_jsonl
from guard_connector_sdk.schema import (
    MAX_BODY_BYTES,
    MAX_EVENTS,
    InputError,
    decode_json,
    encode_request,
    validate_event,
    validate_request,
)


EVENT = {
    "timestamp": "2025-01-15T09:30:00.123-05:00",
    "api_route": "/demo/orders/{id}?token=private#fragment",
    "http_method": "get",
    "status_code": 200,
}


class SchemaTests(unittest.TestCase):
    def test_normalized_metadata_and_removed_query(self):
        event = validate_event(EVENT | {
            "latency_ms": 0,
            "request_id": "8F0070DE-5EBD-4B09-93BA-81581C0F308E",
        })
        self.assertEqual(event, {
            "timestamp": "2025-01-15T14:30:00.123000Z",
            "api_route": "/demo/orders/{id}",
            "http_method": "GET",
            "status_code": 200,
            "request_id": "8f0070de-5ebd-4b09-93ba-81581c0f308e",
            "latency_ms": 0,
        })
        self.assertIn("token=private", EVENT["api_route"])
        self.assertEqual(validate_event(event), event)
        self.assertEqual(
            validate_event(EVENT | {"timestamp": "2025-01-15T14:30:00.1Z"})["timestamp"],
            "2025-01-15T14:30:00.100000Z",
        )

    def test_unknown_fields_and_metadata_values_are_not_echoed(self):
        event = EVENT | {"untrusted-secret-name": "private-canary"}
        with self.assertRaises(InputError) as caught:
            validate_event(event)
        self.assertNotIn("untrusted-secret-name", str(caught.exception))
        self.assertNotIn("private-canary", str(caught.exception))
        for key in ("body", "prompt", "headers", "organization_id", "organization_unit_id"):
            with self.subTest(key=key), self.assertRaises(InputError):
                validate_event(EVENT | {key: "private"})

    def test_rejects_paths_that_could_be_misinterpreted(self):
        for route in (
            "https://example.invalid/api", "//example.invalid/api", "/v1//orders",
            "/safe/../private", "/unsafe%2fsegment", "/a\\b", "/bad?x=1\n",
            "/space in path", "/unicode/é", "relative", "/x:123",
        ):
            with self.subTest(route=route), self.assertRaises(InputError):
                validate_event(EVENT | {"api_route": route})
        for route in ("/", "/v1/", "/a#x?legacy"):
            with self.subTest(route=route):
                self.assertEqual(validate_event(EVENT | {"api_route": route})["api_route"],
                                 route.split("#")[0])

    def test_validation_enforces_value_types_and_bounds(self):
        invalid = {
            "timestamp": ["2025-01-15", "2025-01-15T09:30:00", "2025-01-15T09:30:00+00:60",
                          "2025-02-29T09:30:00Z", "0001-01-01T00:00:00+12:00",
                          "2025-01-15T09:30:00.1234567Z"],
            "status_code": [True, 99, 600, "200"],
            "latency_ms": [1.0, False, -1, 2**31],
            "request_bytes": [2**63, -1],
            "response_bytes": [2**63, None],
            "http_method": ["", "GET ", "GET1", "G" * 17],
            "request_id": ["not-a-uuid", None],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(InputError):
                    validate_event(EVENT | {field: value})

    def test_envelope_limits_before_and_after_normalization(self):
        with self.assertRaises(InputError):
            validate_request({"events": [], "api_key": "private"})
        with self.assertRaises(InputError):
            encode_request([])
        with self.assertRaises(InputError):
            encode_request([EVENT] * (MAX_EVENTS + 1))
        self.assertLess(len(encode_request([EVENT] * MAX_EVENTS)), MAX_BODY_BYTES)
        large = EVENT | {"api_route": "/" + "x" * 2047}
        with self.assertRaisesRegex(InputError, "normalized request exceeds"):
            validate_request({"events": [large] * 150})

    def test_json_decoder_rejects_duplicate_keys_nonfinite_and_bad_encoding(self):
        raw_values = (
            b'{"dup":"private","dup":"private"}', b'{"a": {"dup":1,"dup":2}}',
            b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":1e999}',
            b'\xff', b'[' * 1600 + b'0',
            b'{"secret":"private" broken}', '{"a":1}'.encode("utf-16"),
        )
        for case, raw in enumerate(raw_values):
            with self.subTest(case=case):
                with self.assertRaises(InputError) as caught:
                    decode_json(raw)
                self.assertNotIn("private", str(caught.exception))


class ReferenceTests(unittest.TestCase):
    def test_fixture_rows_can_become_one_complete_batch(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "events.jsonl"
        batch = read_jsonl(path)
        self.assertEqual(len(batch), 2)
        request = encode_request(batch)
        self.assertNotIn(b"sample_flag", request)
        self.assertEqual(json.loads(request)["events"], batch)

    def test_bad_last_row_fails_preflight_and_reports_line_without_raw_message(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_bytes(json.dumps(EVENT).encode() + b'\n\n{"private":oops}\n')
            with self.assertRaisesRegex(InputError, "^line 3: invalid UTF-8 JSON$"):
                read_jsonl(path)

    def test_file_bytes_and_event_count_are_independently_bounded(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_bytes(b" " * (MAX_BODY_BYTES + 1))
            with self.assertRaisesRegex(InputError, "byte limit"):
                read_jsonl(path)
            path.write_bytes((json.dumps(EVENT).encode() + b"\n") * (MAX_EVENTS + 1))
            with self.assertRaisesRegex(InputError, "event limit"):
                read_jsonl(path)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO required")
    def test_nonregular_input_is_rejected_without_waiting_for_a_writer(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stream"
            os.mkfifo(path)
            with self.assertRaisesRegex(InputError, "regular JSONL file"):
                read_jsonl(path)

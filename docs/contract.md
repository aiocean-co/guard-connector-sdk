# API observation contract

The adapter accepts UTF-8 JSON Lines with one object per non-blank line. One input file is one POST to `/v1/ingest/events` with content type `application/json`. The JSON request has a single `events` array. The file and normalized request are each limited to 256 KiB with 1 to 500 events.

| Field | Meaning |
| --- | --- |
| `timestamp` | Observation time in RFC 3339 with an explicit offset, normalized to UTC. |
| `api_route` | Relative API path or route template such as `/orders/{id}`. Query and fragment text is removed before sending. Use templates so free text, identifiers, or credentials never become path segments. |
| `http_method` | 1 to 16 ASCII letters, normalized to uppercase. |
| `status_code` | Integer HTTP status from 100 to 599. |
| `request_id` | Optional hyphenated UUID for the event at its source. When omitted, Guard assigns the request's trace ID. |
| `latency_ms`, `request_bytes`, `response_bytes` | Optional nonnegative integer measurements. |

The SDK rejects unknown fields, duplicate JSON keys, non-finite numbers, unsafe route shapes, and out-of-range values. A bad event rejects the entire file during preflight. Authorization headers, payloads, full external URLs, user identifiers, and tenant-selection data are outside the observation contract. [JSON schema](../examples/api-observation.schema.json)

## Delivery and acknowledgements

- Use the approved HTTPS intake endpoint. The client prevents redirects and inherited proxy configuration. A separate explicit switch enables HTTP on a numeric loopback address for local tests.
- Pass the bearer credential in `GUARD_CONNECTOR_API_KEY` for the CLI or to the SDK client from a trusted secret source. The credential controls intake authority and exact tenant scope.
- The client sets a fresh `x-request-id` for each POST. Success requires HTTP 200, the response trace ID, the expected organization, and the exact accepted count. The receipt also carries Guard's reported downstream enqueue state.
- The organization check occurs **after** delivery. Confirm the credential's exact organization and unit before sending. The intake response currently omits the unit. Verify the selected unit in the Guard workspace.
- CLI exit codes are 0 for valid input or confirmed success, 2 for invalid input or configuration, 3 for a recognized HTTP rejection, and 4 for an unknown delivery outcome. The client attempts one POST. A timeout, redirect or unusable acknowledgement needs reconciliation before retry. The CLI logs a trace ID and safe error code for failed sends.

## Source boundaries

An API observation describes a request seen by the connector. Source timestamps and route labels remain source claims. A successful receipt confirms intake of those observations. Policy decisions, agent identity, successful execution and data leakage require evidence from the appropriate Guard workflows.

The loopback test receiver validates the same input and acknowledgement shape with a single test organization and token. Use it to test adapters and transport. Production scope, policy and evidence checks are enforced by Guard.

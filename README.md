# QubitOceans Guard Connector SDK

A standalone Python SDK for sending metadata about HTTP requests to QubitOceans Guard. The first reference connector reads an entire JSON Lines file, validates the observations, and sends one batch. The local test receiver implements the same acknowledgement shape, so you can build a connector without a Guard account.

The SDK requires Python 3.11 or newer and has no runtime dependencies outside the standard library. Code is provided under [MPL-2.0](LICENSE).

## Try the reference connector

From the repository root, validate the sample events:

```sh
python3 -m guard_connector_sdk validate --input examples/events.jsonl
```

Start the test receiver in a separate terminal. Use a disposable test token:

```sh
GUARD_CONNECTOR_TEST_TOKEN=local-demo-only python3 -m guard_connector_sdk.receiver
```

Send the example to the receiver:

```sh
GUARD_CONNECTOR_API_KEY=local-demo-only python3 -m guard_connector_sdk send \
  --input examples/events.jsonl \
  --endpoint http://127.0.0.1:8877/v1/ingest/events \
  --organization-id 00000000-0000-4000-8000-000000000001 \
  --allow-loopback-http
```

The CLI prints an accepted count and a request ID. The receiver validates and acknowledges events in memory and discards their contents. Stop it with Ctrl+C when finished.

To install the CLI commands in a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
guard-connector --help
guard-test-receiver --help
```

## Connect to Guard

1. Open the intended organization and unit in Guard. In **API Keys → New API Key**, select **Guard Connector SDK (ingest only)** under **Key purpose**. This creates a dedicated key with only `events:ingest`. Limit its lifetime and store it in a secret manager or protected environment. If your Guard install lacks this option, upgrade Guard to a build that supports the scope.
2. Produce a JSONL file with fresh UTC timestamps and route templates for the API you own. Use the [API observation schema](examples/api-observation.schema.json) and [contract](docs/contract.md).
3. Set `GUARD_CONNECTOR_API_KEY` securely in the invoking environment. Run the same send command with the Guard HTTPS intake URL and the expected organization UUID. For desktop development, numeric loopback HTTP requires the explicit flag from the local example.
4. Sign in to Guard with an operator account and open **API Discovery** in the same organization and unit. Choose a window containing the observations and look up their method and route. Match the accepted count and preserve the request ID for support or reconciliation. Guard derives organization and unit from the connector credential. The acknowledgement checks the organization, and API Discovery confirms scope and retained observations.

The `events:ingest` key authorizes submitting API observations to `/v1/ingest/events`. Model proxying and API Discovery reads require separate credentials with the appropriate permissions. SDK v0.1.1 includes this setup guide. Existing v0.1.0 clients can also use the dedicated key.

This version sends API observations through `/v1/ingest/events`. Its acknowledgement reports persistence and whether downstream analysis was enqueued. Findings, signed cloud or endpoint evidence, and approvals use their own Guard workflows.

## Build a connector

Convert your source records into a list of observations, then call `GuardClient.send(events)`. Each dict accepts a timestamp, route template, HTTP method, response status, and a small allowlist of timing or byte measurements. Populate fields from your source's typed metadata instead of forwarding an entire log record.

```python
from datetime import datetime, timezone
import os

from guard_connector_sdk.transport import GuardClient

client = GuardClient(
    "https://your-guard-intake.example/v1/ingest/events",
    organization_id=os.environ["GUARD_ORGANIZATION_ID"],
    api_key=os.environ["GUARD_CONNECTOR_API_KEY"],
)
receipt = client.send([{
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "api_route": "/orders/{order_id}",
    "http_method": "GET",
    "status_code": 200,
}])
print(receipt.accepted_events)
```

If a send returns an unknown outcome, look up the original records before sending again. Request IDs support tracing. Guard's current API intake counts a repeated submission as another observation.

```sh
python3 -m unittest discover -s tests -v
```

See [contributing](CONTRIBUTING.md) to propose a connector and [security](SECURITY.md) to report a vulnerability.

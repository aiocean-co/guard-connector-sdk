# SPDX-License-Identifier: MPL-2.0
"""Command line entry point for the JSONL reference connector."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from . import __version__
from .reference import read_jsonl
from .schema import InputError
from .transport import API_KEY_ENV, DeliveryError, GuardClient


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate an entire file locally")
    send = commands.add_parser("send", help="Validate and submit one batch")
    for command in (validate, send):
        command.add_argument("--input", required=True, type=Path, help="JSONL file")
    send.add_argument("--endpoint", required=True, help="Complete Guard intake URL")
    send.add_argument("--organization-id", required=True, help="Expected organization UUID")
    send.add_argument("--allow-loopback-http", action="store_true",
                      help="Allow numeric loopback HTTP for local testing")
    args = parser.parse_args(argv)
    try:
        events = read_jsonl(args.input)
        if args.command == "validate":
            print(json.dumps({"status": "valid", "events": len(events)}))
            return 0
        receipt = GuardClient(
            args.endpoint, organization_id=args.organization_id,
            api_key=os.getenv(API_KEY_ENV, ""),
            allow_loopback_http=args.allow_loopback_http,
        ).send(events)
        print(json.dumps({"status": "accepted", **asdict(receipt)}))
        return 0
    except InputError as error:
        print(json.dumps({"status": "invalid_input", "error": str(error)}), file=sys.stderr)
        return 2
    except DeliveryError as error:
        result = {"status": error.outcome, "error": error.reason,
                  "request_id": error.request_id}
        if error.http_status is not None:
            result["http_status"] = error.http_status
        print(json.dumps(result), file=sys.stderr)
        return 3 if error.outcome == "rejected" else 4


if __name__ == "__main__":
    raise SystemExit(main())

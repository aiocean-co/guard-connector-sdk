# SPDX-License-Identifier: MPL-2.0
"""Reference adapter for metadata-only API observations in a JSONL file."""

import os
import stat
from pathlib import Path

from .schema import MAX_BODY_BYTES, MAX_EVENTS, InputError, decode_json, validate_request


def _read_file(path: Path) -> bytes:
    # Nonblocking open lets the file-type check reject pipes without waiting
    # for a writer. Size checks still bound the actual read if a file grows.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise InputError("input must be a regular JSONL file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read(MAX_BODY_BYTES + 1)
        finally:
            os.close(fd)
    except OSError:
        raise InputError("unable to read the input JSONL file") from None


def read_jsonl(path: Path) -> list[dict]:
    """Preflight an entire file, returning one complete, normalized batch."""
    data = _read_file(path)
    if len(data) > MAX_BODY_BYTES:
        raise InputError("input exceeds the 256 KiB byte limit")
    events = []
    for line_number, line in enumerate(data.split(b"\n"), start=1):
        if not line.strip():
            continue
        if len(events) == MAX_EVENTS:
            raise InputError("input exceeds the 500 event limit")
        try:
            events.append(decode_json(line))
        except InputError as error:
            raise InputError(f"line {line_number}: {error}") from None
    return validate_request({"events": events})

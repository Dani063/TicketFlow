#!/usr/bin/env python3
"""Build the deployable help-center snapshot used by data migrations."""

import argparse
import base64
import gzip
import hashlib
import json
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "resources" / "help_content" / "help_centers.json"
DEFAULT_DESTINATION = (
    PROJECT_ROOT / "src" / "app" / "migration_data" / "help_content_snapshot_0052.py"
)


def build_snapshot(source: Path, destination: Path) -> None:
    raw = source.read_bytes()
    json.loads(raw)

    digest = hashlib.sha256(raw).hexdigest()
    encoded = base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")
    payload_lines = "\n".join(
        f'    b"{line}"' for line in textwrap.wrap(encoded, width=88)
    )
    generated = f'''\
"""Generated deployable snapshot for help-center migrations.

Do not edit manually. Regenerate with scripts/build_help_content_snapshot.py.
"""

import base64
import gzip
import hashlib
import json


SNAPSHOT_SHA256 = "{digest}"
_PAYLOAD = (
{payload_lines}
)


def load_snapshot():
    raw = gzip.decompress(base64.b64decode(_PAYLOAD, validate=True))
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SNAPSHOT_SHA256:
        raise RuntimeError("El snapshot integrado de centros de ayuda no es válido")
    return json.loads(raw.decode("utf-8"))
'''

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(generated, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    build_snapshot(args.source.resolve(), args.destination.resolve())


if __name__ == "__main__":
    main()

"""Start the bundled PostgreSQL 16 cluster that R8 measures against.

The sandbox has no Docker and no system PostgreSQL; `pgserver` ships real PostgreSQL 16.2 binaries so the
load test runs against the same engine family the product deploys on — not SQLite, which R8 forbids for
capacity claims.

Usage:  python scripts/load/pg_start.py [/tmp/r8pg]
"""

from __future__ import annotations

import pathlib
import sys

import pgserver

PGDATA = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r8pg")

if __name__ == "__main__":
    server = pgserver.get_server(PGDATA, cleanup_mode=None)
    print(server.psql("show max_connections; show shared_buffers; show server_version;"))
    print("URI:", server.get_uri())

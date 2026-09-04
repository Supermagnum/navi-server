#!/usr/bin/env python3
"""Read-only static file server for Navi published packs.

Serves ONLY a configured document root (default: data/published).
GET and HEAD only — every other method returns 405.
No CGI, no uploads, no path escape outside the document root.

Usage:
  /media/navi/navi-server/http/static-packs-server.py
  /media/navi/navi-server/http/static-packs-server.py --port 8097 \\
      --root /media/navi/navi-server/data/published
"""

from __future__ import annotations

import argparse
import os
import posixpath
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote


class PacksHandler(SimpleHTTPRequestHandler):
    server_version = "NaviPacksStatic/1.0"

    def do_GET(self) -> None:  # noqa: N802
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        super().do_HEAD()

    def do_POST(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_method()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_method()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reject_method()

    def do_TRACE(self) -> None:  # noqa: N802
        self._reject_method()

    def _reject_method(self) -> None:
        self.send_error(405, "Method Not Allowed")

    def translate_path(self, path: str) -> str:
        # Ignore query/fragment; map URL path under document root only.
        path = path.split("?", 1)[0]
        path = path.split("#", 1)[0]
        path = unquote(path)
        path = posixpath.normpath(path)
        words = [w for w in path.split("/") if w not in ("", ".")]
        if any(w == ".." for w in words):
            # Should already be collapsed by normpath; belt-and-braces.
            return os.path.join(self.directory, "\0-forbidden")
        root = os.path.realpath(self.directory)
        candidate = os.path.realpath(os.path.join(root, *words))
        if candidate != root and not candidate.startswith(root + os.sep):
            return os.path.join(root, "\0-forbidden")
        return candidate

    def list_directory(self, path: str):
        # Pack-tree listing is acceptable; keep SimpleHTTP behavior.
        return super().list_directory(path)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default="/media/navi/navi-server/data/published",
        help="document root (must be the published/ tree only)",
    )
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8097)
    args = parser.parse_args()

    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        print(f"document root missing: {root}", file=sys.stderr)
        return 1
    # Refuse accidental roots that are not .../published
    if os.path.basename(root) != "published":
        print(
            f"refusing to serve root that is not named 'published': {root}",
            file=sys.stderr,
        )
        return 1

    handler = partial(PacksHandler, directory=root)
    httpd = ThreadingHTTPServer((args.bind, args.port), handler)
    print(
        f"Navi packs static server GET/HEAD only root={root} "
        f"http://{args.bind}:{args.port}/",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

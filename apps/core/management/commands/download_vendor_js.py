"""Download vendor JS files (Tailwind, HTMX, Alpine) to local static.

Every download is bounded (timeout + size cap), verified against a required
SHA-256 digest supplied on the command line, and written via a temporary file
plus atomic rename, so a failed or tampered download never leaves a partial
file behind.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 64 * 1024

FILES: dict[str, str] = {
    "htmx.min.js": "https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/htmx.min.js",
    "htmx-loading-states.min.js": "https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/ext/loading-states.js",
    "alpine.min.js": "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js",
}


class Command(BaseCommand):
    help = "Download vendor JS files (Tailwind, HTMX, Alpine) to local static with digest verification."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sha256",
            action="append",
            default=[],
            metavar="NAME=DIGEST",
            help="Expected SHA-256 digest for a vendor file, repeat once per file (e.g. --sha256 htmx.min.js=abc123...).",
        )

    def handle(self, *args, **options):
        expected_digests: dict[str, str] = {}
        for entry in options["sha256"]:
            if "=" not in entry:
                raise CommandError(f"Invalid --sha256 entry (expected NAME=DIGEST): {entry}")
            name, digest = entry.split("=", 1)
            digest = digest.strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise CommandError(f"--sha256 digest for {name} is not a 64-character hex string")
            expected_digests[name.strip()] = digest

        dest_dir = Path(settings.STATIC_DIR) / "js"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name, url in FILES.items():
            dest = dest_dir / name
            if dest.exists():
                self.stdout.write(f"  {name} already exists")
                continue
            expected = expected_digests.get(name)
            if expected is None:
                raise CommandError(f"--sha256 digest is required for {name}; run with --sha256 {name}=<digest>")
            self.stdout.write(f"Downloading {name}...")
            try:
                self._download_verified(name, url, dest, expected)
                self.stdout.write(self.style.SUCCESS(f"  Saved {dest}"))
            except (CommandError, OSError, urllib.error.URLError) as exc:
                self.stdout.write(self.style.ERROR(f"  Failed: {exc}"))

    def _download_verified(self, name: str, url: str, dest: Path, expected_digest: str) -> None:
        digest = hashlib.sha256()
        total = 0
        fd, tmp_path = tempfile.mkstemp(dir=str(dest.parent), suffix=".part")
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                with os.fdopen(fd, "wb") as out:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise CommandError(
                                f"Download of {name} exceeded the {MAX_DOWNLOAD_BYTES}-byte size cap"
                            )
                        digest.update(chunk)
                        out.write(chunk)
            actual = digest.hexdigest()
            if not hmac.compare_digest(actual, expected_digest):
                raise CommandError(
                    f"SHA-256 mismatch for {name}: expected {expected_digest}, got {actual}"
                )
            os.replace(tmp_path, dest)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

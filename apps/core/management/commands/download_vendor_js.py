from __future__ import annotations

import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

FILES: dict[str, str] = {
    "tailwind.min.js": "https://cdn.tailwindcss.com/3.4.16",
    # "tailwind.min.js" alternate: "https://cdn.tailwindcss.com/3.4.16",
    "htmx.min.js": "https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/htmx.min.js",
    "htmx-loading-states.min.js": "https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/ext/loading-states.js",
    "alpine.min.js": "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js",
}


class Command(BaseCommand):
    help = "Download vendor JS files (Tailwind, HTMX, Alpine) to local static."

    def handle(self, *args, **options):
        dest_dir = Path(settings.STATIC_DIR) / "js"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name, url in FILES.items():
            dest = dest_dir / name
            if dest.exists():
                self.stdout.write(f"  {name} already exists")
                continue
            self.stdout.write(f"Downloading {name}...")
            try:
                urllib.request.urlretrieve(url, dest)
                self.stdout.write(self.style.SUCCESS(f"  Saved {dest}"))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  Failed: {exc}"))

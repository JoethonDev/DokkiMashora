"""ASGI config for DokkiMashora LMS project."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Compose supplies .env values as environment variables; the nonroot app may
# not be allowed to reopen the private host file mounted under /app.
dotenv_path = Path(__file__).resolve().parent.parent / ".env"
if dotenv_path.is_file() and os.access(dotenv_path, os.R_OK):
    load_dotenv(dotenv_path)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.asgi import get_asgi_application  # noqa: E402

application = get_asgi_application()  # noqa: E402

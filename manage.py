#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    # Docker Compose injects the same .env values into the process. The
    # bind-mounted file may be chmod 600 and unreadable to the nonroot app.
    dotenv_path = Path(__file__).resolve().parent / ".env"
    if dotenv_path.is_file() and os.access(dotenv_path, os.R_OK):
        load_dotenv(dotenv_path)
except ImportError:
    pass


def main() -> None:
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

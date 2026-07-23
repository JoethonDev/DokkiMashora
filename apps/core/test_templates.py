"""Verify all templates compile without errors."""
import glob
import os

import django
from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
import pytest


@pytest.mark.django_db
def test_all_templates_compile():
    base = str(settings.BASE_DIR / "templates")
    files = glob.glob(f"{base}/**/*.html", recursive=True)
    errors = []
    loaded = 0
    for f in files:
        rel = os.path.relpath(f, base).replace(os.sep, "/")
        try:
            get_template(rel)
            loaded += 1
        except Exception as e:
            errors.append((rel, str(e)))
    assert not errors, f"{len(errors)} template(s) failed:\n" + "\n".join(
        f"{r}: {e}" for r, e in errors
    )
    assert loaded >= 113, f"Expected at least 113 templates, got {loaded}"

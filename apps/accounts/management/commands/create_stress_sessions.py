"""Mint authenticated sessions for the nginx-facing stress harness."""

from __future__ import annotations

import json
import secrets

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create real authenticated sessions for stress users and emit JSON."

    def add_arguments(self, parser):
        parser.add_argument("--users", type=int, default=400)

    def handle(self, *args, **options):
        user_model = get_user_model()
        sessions = {}
        for user in user_model.objects.filter(
            email__regex=r"^stress\d+@dokkimashora\.test$", is_active=True,
        ).order_by("pk")[: options["users"]]:
            session = SessionStore()
            session["_auth_user_id"] = str(user.pk)
            session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
            session["_auth_user_hash"] = user.get_session_auth_hash()
            session.create()
            sessions[str(int(user.email[6:10]))] = {
                "sessionid": session.session_key,
                "csrftoken": secrets.token_hex(32),
            }
        self.stdout.write(json.dumps(sessions))

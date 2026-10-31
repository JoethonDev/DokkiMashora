"""Gunicorn config with queue visibility for Take burst investigation.

Request access logging is owned by nginx so one-time token paths can be
redacted and every request carries a single request ID shared with Django and
the audit trail. Gunicorn keeps its error log for worker/exception visibility.
"""

import os
import time

workers = int(os.getenv("GUNICORN_WORKERS", "3"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "gthread")
backlog = int(os.getenv("GUNICORN_BACKLOG", "2048"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "65"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
accesslog = None
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")


def pre_request(worker, req):
    # Store wall-clock when Gunicorn hands request to worker thread.
    req._gunicorn_pre_request_msec = time.time() * 1000.0
    hdr = None
    try:
        if hasattr(req, "headers"):
            for key, value in req.headers:
                if key.lower() == "x-request-start":
                    hdr = value
                    break
    except Exception:
        hdr = None
    if hdr:
        try:
            nginx_ms = float(hdr) * 1000.0 if "." in hdr else float(hdr)
            queue_ms = req._gunicorn_pre_request_msec - nginx_ms
            if -5000 < queue_ms < 120000:
                worker.log.info("gunicorn queue_ms=%.1f nginx_start=%s", queue_ms, hdr)
        except Exception:
            pass
    worker.log.debug("pre_request %s %s", req.method, req.path)


def post_request(worker, req, environ, resp):
    pass

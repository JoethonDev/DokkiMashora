"""Gunicorn config with queue visibility for Take burst investigation."""

import os
import time

workers = int(os.getenv("GUNICORN_WORKERS", "3"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "gthread")
backlog = int(os.getenv("GUNICORN_BACKLOG", "2048"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "65"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")
# Include duration (%D microseconds) and nginx-supplied header for queue estimation.
# %({header}i)s may be '-' if header absent.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s "%(a)s" dur=%(D)sus qhdr=%({X-Request-Start}i)s worker=%(p)s'


def pre_request(worker, req):
    # Store wall-clock when Gunicorn hands request to worker thread.
    # This is after any accept/thread-queue wait inside Gunicorn.
    req._gunicorn_pre_request_msec = time.time() * 1000.0
    # If nginx forwarded its receive time, log the delta as rough queue estimate.
    hdr = None
    try:
        hdr = req.headers.find("X-Request-Start", None)  # type: ignore[attr-defined]
        # req.headers is list of tuples in gunicorn Request
        if hdr is None and hasattr(req, "headers"):
            for k, v in getattr(req, "headers", []):
                if k.lower() == "x-request-start":
                    hdr = v
                    break
    except Exception:
        hdr = None
    if hdr:
        try:
            nginx_ms = float(hdr) * 1000.0 if "." in hdr else float(hdr)
            # Header from nginx is $msec (seconds.millis). Convert to ms if needed.
            # If hdr looks like seconds (e.g. 1725...), assume seconds*1000.
            queue_ms = req._gunicorn_pre_request_msec - nginx_ms
            if -5000 < queue_ms < 120000:
                worker.log.info("gunicorn queue_ms=%.1f nginx_start=%s", queue_ms, hdr)
        except Exception:
            pass
    worker.log.debug("pre_request %s %s", req.method, req.path)


def post_request(worker, req, environ, resp):
    # Lightweight post hook; access log already captures duration.
    pass

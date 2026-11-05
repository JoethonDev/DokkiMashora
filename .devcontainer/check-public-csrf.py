#!/usr/bin/env python3
"""Safely diagnose CSRF cookies across the public Cloudflare gateway.

Does not print cookies, hidden CSRF tokens, response bodies, or credentials.
Makes one login GET and one intentionally invalid login POST per hostname.
"""

import http.cookiejar
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attributes = dict(attrs)
        if attributes.get("name") == "csrfmiddlewaretoken":
            self.token = attributes.get("value")


def request_with_status(opener, req):
    try:
        return opener.open(req, timeout=15)
    except urllib.error.HTTPError as exc:
        return exc


def check(origin):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    url = origin + "/accounts/login/"
    response = request_with_status(
        opener,
        urllib.request.Request(url, headers={"Cache-Control": "no-store"}),
    )
    with response:
        status = response.status
        headers = response.headers
        body = response.read().decode("utf-8", "replace")

    parser = CsrfTokenParser()
    parser.feed(body)
    cookie_names = {cookie.name for cookie in jar}
    set_cookie_names = [v.split("=", 1)[0].strip() for v in headers.get_all("Set-Cookie", [])]
    has_csrf_cookie = "csrftoken" in cookie_names
    sets_csrf = "csrftoken" in set_cookie_names
    print(f"{origin}: GET={status}, Set-Cookie(csrftoken)={sets_csrf}, "
          f"browser-style cookie stored={has_csrf_cookie}, HTML token={bool(parser.token)}")

    if status != 200 or not parser.token or not has_csrf_cookie:
        print("  FAIL BEFORE POST: inspect public GET response and browser cookie restrictions.")
        return False

    data = urllib.parse.urlencode({
        "username": "invalid-demo-user@example.invalid",
        "password": "intentionally-incorrect",
        "csrfmiddlewaretoken": parser.token,
    }).encode()
    post = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": origin,
            "Referer": url,
        },
    )
    # CookieJar adds the matching cookie to the outgoing request.
    response = request_with_status(opener, post)
    with response:
        status = response.status
        response.read()
    print(f"{origin}: correctly tokened anonymous login POST={status}"
          " (403 indicates CSRF failure; 200/302 indicates CSRF accepted).")
    return status in (200, 302)


def main():
    host = os.environ.get("CODESPACE_NAME", "")
    origins = ["https://demo.joethon.dev"]
    if host:
        origins.append(f"https://{host}-80.app.github.dev")
    failures = 0
    for origin in origins:
        try:
            if not check(origin):
                failures += 1
        except Exception as exc:
            print(f"{origin}: network/transport error {type(exc).__name__}")
            failures += 1
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

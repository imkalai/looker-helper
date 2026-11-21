"""Looker API calls. Used to check a connection; more features (send to Looker, real numbers) come later."""

import fnmatch
import ipaddress
import socket
from urllib.parse import quote, urlparse

import httpx

from app.config import get_settings


def check_looker_url(url: str, resolve: bool = False) -> str | None:
    """Return a plain error message if the server shouldn't call this address, else None.

    Users type the Looker address, and the server then calls it. To stop that being used to reach
    internal services (SSRF): HTTPS only, an optional host allowlist, and no loopback, link-local
    (including the cloud metadata server), multicast or unspecified addresses.
    """
    parts = urlparse(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return "The Looker address should start with https://"
    if parts.username or parts.password:
        return "Leave the user name and password out of the Looker address."
    allowed = get_settings().allowed_looker_hosts
    if allowed and not any(fnmatch.fnmatch(host, pattern) for pattern in allowed):
        return "That Looker address isn't on your organisation's list of allowed Looker hosts."
    if resolve:
        try:
            infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return "Couldn't find that Looker address. Check it, and whether your network allows it."
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                return "That address points to an internal system and can't be used as a Looker address."
    return None


def test_connection(base_url: str, client_id: str, client_secret: str, bq_connection: str, timeout: float = 15.0) -> tuple[str, str]:
    """Return (status, plain message). status is ok | failed | untested."""
    problem = check_looker_url(base_url)
    if problem:
        return "failed", problem
    if not client_id or not client_secret:
        return "untested", "Saved. Add an API client ID and secret to check it against Looker."
    problem = check_looker_url(base_url, resolve=True)
    if problem:
        return "failed", problem
    api = base_url.rstrip("/") + "/api/4.0"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            r = client.post(f"{api}/login", data={"client_id": client_id, "client_secret": client_secret})
            if r.status_code in (401, 403, 404):
                return "failed", "Looker didn't accept the API client ID or secret."
            r.raise_for_status()
            token = r.json().get("access_token", "")
            c = client.get(f"{api}/connections/{quote(bq_connection, safe='')}", headers={"Authorization": f"token {token}"})
            if c.status_code == 404:
                return "failed", f"Connected to Looker, but there's no BigQuery connection called “{bq_connection}”."
            c.raise_for_status()
            return "ok", f"Connected. Found “{bq_connection}”."
    except httpx.ConnectError:
        return "failed", "Couldn't reach that Looker address. Check it, and whether your network allows it."
    except httpx.TimeoutException:
        return "failed", "Looker took too long to answer. Try again."
    except (httpx.HTTPError, ValueError) as exc:
        return "failed", f"Looker returned an error ({exc.__class__.__name__})."

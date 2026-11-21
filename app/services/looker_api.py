"""Looker API calls. Used to check a connection; more features (send to Looker, real numbers) come later."""

import httpx


def test_connection(base_url: str, client_id: str, client_secret: str, bq_connection: str, timeout: float = 15.0) -> tuple[str, str]:
    """Return (status, plain message). status is ok | failed | untested."""
    if not base_url.startswith(("https://", "http://")):
        return "failed", "The Looker address should start with https://"
    if not client_id or not client_secret:
        return "untested", "Saved. Add an API client ID and secret to check it against Looker."
    api = base_url.rstrip("/") + "/api/4.0"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{api}/login", data={"client_id": client_id, "client_secret": client_secret})
            if r.status_code in (401, 403, 404) and r.request.url.path.endswith("/login"):
                return "failed", "Looker didn't accept the API client ID or secret."
            r.raise_for_status()
            token = r.json().get("access_token", "")
            c = client.get(f"{api}/connections/{bq_connection}", headers={"Authorization": f"token {token}"})
            if c.status_code == 404:
                return "failed", f"Connected to Looker, but there's no BigQuery connection called “{bq_connection}”."
            c.raise_for_status()
            return "ok", f"Connected. Found “{bq_connection}”."
    except httpx.ConnectError:
        return "failed", "Couldn't reach that Looker address. Check it, and whether your network allows it."
    except httpx.TimeoutException:
        return "failed", "Looker took too long to answer. Try again."
    except httpx.HTTPError as exc:
        return "failed", f"Looker returned an error ({exc.__class__.__name__})."

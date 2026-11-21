"""End-to-end API flow on PostgreSQL, in demo mode with the mock Cortex.

These tests wipe the database, so they only run inside the throwaway test container:
    docker compose run --rm --build tests
"""

import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import db as dbmod
from app.config import get_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("LH_TEST_ALLOW_RESET") != "1",
    reason="database tests run in Docker: docker compose run --rm --build tests",
)


def _reset_schema() -> None:
    with dbmod.get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LH_METADATA_MODE", "demo")
    monkeypatch.setenv("LH_CORTEX_MODE", "mock")
    monkeypatch.setenv("LH_DEV_USER_EMAIL", "dev.user@example.com")
    monkeypatch.setenv("LH_ADMIN_EMAILS", "")
    get_settings.cache_clear()
    from app import security

    security._fernet.cache_clear()
    dbmod.reset_engine()
    _reset_schema()  # the app's startup then rebuilds it with the migrations
    from app.main import app

    with TestClient(app) as c:
        yield c
    dbmod.reset_engine()
    get_settings.cache_clear()


def test_full_flow(client):
    me = client.get("/api/me").json()
    assert me["email"] == "dev.user@example.com" and me["is_admin"]

    assert any(p["id"] == "acme-analytics" for p in client.get("/api/meta/projects").json())
    datasets = client.get("/api/meta/projects/acme-analytics/datasets").json()
    assert datasets[0]["name"] == "Sales data"
    tables = client.get("/api/meta/projects/acme-analytics/datasets/sales/tables").json()
    assert {"orders", "customers"} <= {t["name"] for t in tables}

    request = "Revenue by region for each month, as a dashboard"
    sugg = client.post("/api/meta/suggest-tables", json={"project": "acme-analytics", "dataset": "sales", "request": request}).json()
    assert sugg["tables"] == ["orders", "customers"]

    conn = client.post("/api/connections", json={
        "name": "Acme Looker (Production)", "base_url": "https://acme.cloud.looker.com", "bq_connection_name": "bq_acme_analytics",
    }).json()
    assert conn["last_test_status"] == "untested" and conn["is_default"]

    s = client.post("/api/sessions", json={
        "project": "acme-analytics", "dataset": "sales", "dataset_label": "Sales data", "tables": sugg["tables"],
        "looker_connection_id": conn["id"], "model_name": "sales",
    }).json()
    sid = s["id"]

    turn = client.post(f"/api/sessions/{sid}/messages", json={"text": request}).json()
    plan_msg = turn["messages"][-1]
    assert plan_msg["kind"] == "plan" and plan_msg["payload"]["status"] == "pending"
    assert "personal data kept hidden" in plan_msg["payload"]["included"]

    turn = client.post(f"/api/sessions/{sid}/messages", json={"text": "Yes, but leave out cancelled orders."}).json()
    kinds = [m["kind"] for m in turn["messages"]]
    assert kinds == ["text", "plan", "package"]
    card = turn["messages"][-1]["payload"]
    assert card["dashboards"] == 1 and card["status"] in ("ready", "review")

    pkg = client.get(f"/api/packages/{card['package_id']}").json()
    paths = {f["path"] for f in pkg["files"]}
    assert "models/sales.model.lkml" in paths
    model = next(f["content"] for f in pkg["files"] if f["kind"] == "model")
    assert "'cancelled'" in model and 'connection: "bq_acme_analytics"' in model
    titles = [c["title"] for c in pkg["checks"]]
    assert "Files are valid" in titles and "Totals are correct" in titles and "Personal data is hidden" in titles

    turn = client.post(f"/api/sessions/{sid}/messages", json={"text": "What does each metric mean?"}).json()
    assert turn["messages"][-1]["kind"] == "text" and "Total revenue" in turn["messages"][-1]["content"]

    z = client.get(f"/api/sessions/{sid}/download")
    assert z.status_code == 200
    assert "models/sales.model.lkml" in zipfile.ZipFile(io.BytesIO(z.content)).namelist()

    listed = client.get("/api/sessions").json()
    assert listed[0]["id"] == sid and listed[0]["package"]["dashboards"] == 1
    assert listed[0]["title"] == "Revenue by region for each month"


def test_change_then_build_creates_version_two(client):
    s = client.post("/api/sessions", json={"project": "acme-analytics", "dataset": "sales", "tables": ["orders", "customers"]}).json()
    sid = s["id"]
    plan = client.post(f"/api/sessions/{sid}/messages", json={"text": "number of orders by channel"}).json()["messages"][-1]
    first = client.post(f"/api/sessions/{sid}/build", json={"plan_message_id": plan["id"]}).json()["messages"][-1]
    changed = client.post(f"/api/sessions/{sid}/messages", json={"text": "make it weekly"}).json()["messages"][-1]
    assert changed["kind"] == "plan"
    second = client.post(f"/api/sessions/{sid}/build", json={"plan_message_id": changed["id"]}).json()["messages"][-1]
    assert second["payload"]["version"] == 2
    d = client.get(f"/api/packages/{second['payload']['package_id']}/diff",
                   params={"against": first["payload"]["package_id"], "path": "models/sales.model.lkml"}).json()
    assert "diff" in d


def test_sessions_are_private(client):
    s = client.post("/api/sessions", json={"project": "acme-analytics", "dataset": "sales", "tables": ["orders"]}).json()
    other = {get_settings().auth_header: "accounts.google.com:someone.else@example.com"}
    assert client.get(f"/api/sessions/{s['id']}", headers=other).status_code == 404
    assert client.get("/api/sessions", headers=other).json() == []


def test_unknown_dataset_gives_friendly_error(client):
    r = client.get("/api/meta/projects/acme-analytics/datasets/nope/tables")
    assert r.status_code == 400 and "wasn't found" in r.json()["detail"]


def test_cortex_settings_admin_only(client):
    other = {get_settings().auth_header: "someone.else@example.com"}
    assert client.put("/api/cortex", json={"endpoint": "https://x"}, headers=other).status_code == 403
    assert client.put("/api/cortex", json={"endpoint": "https://cortex.internal", "api_key": "secret"}).status_code == 200
    got = client.get("/api/cortex").json()
    assert got["endpoint"] == "https://cortex.internal" and got["has_key"] and "secret" not in str(got)


def test_postgres_schema_and_migrations(client):
    with dbmod.get_engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
        assert conn.execute(text(
            "SELECT data_type FROM information_schema.columns WHERE table_name = 'packages' AND column_name = 'summary'"
        )).scalar() == "jsonb"


def test_migrations_downgrade_and_upgrade(client):
    from alembic import command
    from alembic.config import Config

    from app.config import BASE_DIR

    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    with dbmod.get_engine().begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    assert client.get("/api/health").json()["database"] == "ok"

"""Tests for the live /mcp endpoint: unauthenticated bounces with
WWW-Authenticate, authenticated initialise + tools/list + tools/call
happy path, with LoseItClient fully mocked."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from loseit_client.bundle import MealType


def _mcp_headers(token: str | None = None, session: str | None = None) -> dict:
    h = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    if session:
        h["Mcp-Session-Id"] = session
    return h


def _extract_sse_json(body: str) -> dict:
    """FastMCP's streamable-http wraps JSON-RPC responses as single-event
    SSE streams. Pluck the `data:` line out and parse."""
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError(f"no SSE data frame in body: {body!r}")


def test_mcp_401_without_bearer(app):
    from starlette.testclient import TestClient
    with TestClient(app) as c:
        r = c.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            },
            headers=_mcp_headers(),
        )
    assert r.status_code == 401
    www = r.headers.get("www-authenticate", "")
    assert "Bearer" in www
    assert "resource_metadata=" in www


def test_mcp_initialize_with_bearer(authed):
    client, token = authed
    r = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        headers=_mcp_headers(token),
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("mcp-session-id")
    body = _extract_sse_json(r.text)
    assert body["result"]["serverInfo"]["name"] == "loseit"


def test_mcp_tools_list_has_every_tool(authed):
    client, token = authed
    # Init + notify to enter "operational" state
    r = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        headers=_mcp_headers(token),
    )
    session = r.headers["mcp-session-id"]
    client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=_mcp_headers(token, session),
    )
    r = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=_mcp_headers(token, session),
    )
    assert r.status_code == 200
    body = _extract_sse_json(r.text)
    tools = body["result"]["tools"]
    names = {t["name"] for t in tools}
    expected = {
        "list_units",
        "get_day_log",
        "search_foods",
        "search_catalog",
        "search_recipes",
        "barcode_lookup",
        "log_food",
        "log_food_from_barcode",
        "log_calories",
        "edit_log_entry",
        "delete_log_entry",
        "create_recipe",
        "delete_recipe",
    }
    assert expected <= names, f"missing tools: {expected - names}"


def test_list_units_tool_call(authed):
    """Call `list_units` over the wire — this is a read-only tool that
    doesn't touch the mock, so it's a good full-stack smoke test."""
    client, token = authed
    r = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        headers=_mcp_headers(token),
    )
    session = r.headers["mcp-session-id"]
    client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=_mcp_headers(token, session),
    )
    r = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_units", "arguments": {}},
        },
        headers=_mcp_headers(token, session),
    )
    assert r.status_code == 200, r.text
    body = _extract_sse_json(r.text)
    content = body["result"]["content"]
    # FastMCP wraps tool return values in content items; the first should
    # be JSON with the unit list inside its text or structured payload.
    assert content, "tool returned no content"
    # structuredContent may be present depending on SDK version
    structured = body["result"].get("structuredContent")
    if structured:
        units = structured if isinstance(structured, list) else structured.get("result", [])
        assert len(units) >= 40
    else:
        # fall back to parsing the text payload
        text = content[0].get("text", "")
        assert "GRAM" in text or "EACH" in text


def test_get_day_log_delegates_to_client(authed, fake_loseit_client):
    """`get_day_log` should call `client.get_day_log()` with the parsed date."""
    import datetime as dt

    client, token = authed
    fake_loseit_client.get_day_log.return_value = []

    # init + notify
    r = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        headers=_mcp_headers(token),
    )
    session = r.headers["mcp-session-id"]
    client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=_mcp_headers(token, session),
    )

    r = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_day_log", "arguments": {"date": "2026-04-12"}},
        },
        headers=_mcp_headers(token, session),
    )
    assert r.status_code == 200, r.text
    fake_loseit_client.get_day_log.assert_called_once_with(dt.date(2026, 4, 12))


def test_log_calories_delegates(authed, fake_loseit_client):
    from loseit_client.client import LoggedEntry
    from loseit_client import MealType

    fake_loseit_client.log_calories.return_value = LoggedEntry(
        entry_uuid=b"\x01" * 16,
        food_uuid=b"\x02" * 16,
        name="test",
        calories=100,
        meal=MealType.SNACKS,
        server_ack_txn_ids=[1],
        raw_response_fields=[1],
    )

    client, token = authed
    r = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        headers=_mcp_headers(token),
    )
    session = r.headers["mcp-session-id"]
    client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=_mcp_headers(token, session),
    )

    r = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "log_calories",
                "arguments": {
                    "name": "test meal",
                    "calories": 250,
                    "meal": "dinner",
                    "fat_g": 10,
                    "carbohydrate_g": 30,
                    "protein_g": 15,
                },
            },
        },
        headers=_mcp_headers(token, session),
    )
    assert r.status_code == 200, r.text
    call = fake_loseit_client.log_calories.call_args
    assert call.kwargs["name"] == "test meal"
    assert call.kwargs["calories"] == 250
    assert call.kwargs["fat"] == 10
    assert call.kwargs["carbohydrate"] == 30
    assert call.kwargs["protein"] == 15
    assert call.kwargs["meal"] == MealType.DINNER

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
        "refresh_database",
        "search_foods",
        "search_catalog",
        "search_recipes",
        "barcode_lookup",
        "log_food",
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


def _initialize(client, token):
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
    return session


def _call_tool(client, token, session, name: str, args: dict, *, request_id: int = 99):
    return client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
        headers=_mcp_headers(token, session),
    )


def test_log_food_unknown_uuid_raises_helpful_error(authed, fake_loseit_client):
    """A uuid that hasn't been seen via search/lookup should produce a
    pointed error telling the LLM what to call first."""
    # Local lookup returns None
    fake_loseit_client.database.return_value.get_food_by_uuid.return_value = None

    client, token = authed
    session = _initialize(client, token)

    r = _call_tool(
        client, token, session,
        "log_food",
        {"food_uuid": "00" * 16, "meal": "snacks"},
    )
    assert r.status_code == 200, r.text  # MCP errors come back inside the JSON-RPC body
    body = _extract_sse_json(r.text)
    assert body["result"].get("isError") is True, body
    text = body["result"]["content"][0]["text"]
    assert "search_catalog" in text or "search_foods" in text or "barcode_lookup" in text


def test_log_food_routes_catalog_uuid_via_cache(authed, fake_loseit_client):
    """search_catalog populates the in-process catalog cache; a follow-up
    log_food with the catalog uuid should resolve via the cache and
    delegate to client.log_food_from_catalog."""
    from loseit_client import Food, FoodNutrients, FoodServingSize, MealType
    from loseit_client.client import LoggedEntry

    catalog_food = Food(
        unique_id=b"\xab" * 16,
        name="Banana",
        brand_name="",
        category="Fruit",
        language_tag="en-US",
        nutrients=FoodNutrients(base_units=1, calories=89, fat=0.3, carbohydrates=23, protein=1.1),
        servings=[FoodServingSize(size=1, measure_id=5, measure_singular="Each", measure_plural="Each")],
    )
    fake_loseit_client.search_catalog.return_value = [catalog_food]
    fake_loseit_client.database.return_value.get_food_by_uuid.return_value = None
    fake_loseit_client.log_food_from_catalog.return_value = LoggedEntry(
        entry_uuid=b"\x99" * 16,
        food_uuid=catalog_food.unique_id,
        name="Banana",
        calories=89,
        meal=MealType.BREAKFAST,
        server_ack_txn_ids=[1],
        raw_response_fields=[1],
    )

    client, token = authed
    session = _initialize(client, token)

    # Populate the cache via search_catalog
    r = _call_tool(client, token, session, "search_catalog", {"query": "banana"}, request_id=10)
    assert r.status_code == 200, r.text

    # Now log_food with the catalog uuid — should hit log_food_from_catalog
    r = _call_tool(
        client, token, session,
        "log_food",
        {"food_uuid": (b"\xab" * 16).hex(), "meal": "breakfast"},
        request_id=11,
    )
    assert r.status_code == 200, r.text
    body = _extract_sse_json(r.text)
    assert body["result"].get("isError") is not True, body

    fake_loseit_client.log_food_from_catalog.assert_called_once()
    fake_loseit_client.log_food.assert_not_called()
    args = fake_loseit_client.log_food_from_catalog.call_args
    assert args.args[0].name == "Banana"
    assert args.kwargs["meal"] == MealType.BREAKFAST


def test_refresh_database_tool_calls_client(authed, fake_loseit_client):
    client, token = authed
    session = _initialize(client, token)
    r = _call_tool(client, token, session, "refresh_database", {})
    assert r.status_code == 200, r.text
    fake_loseit_client.refresh_database.assert_called_once()


def test_log_food_servings_passed_through_to_catalog(authed, fake_loseit_client):
    """Regression for the '2 carrots → 2 grams' bug: when a user wants
    `servings=2` of a catalog food whose serving is in grams, the MCP
    layer must pass `servings=2` (not `quantity=2`) to the client, so
    the client multiplies by the serving size instead of treating 2
    as a raw gram count."""
    from loseit_client import Food, FoodNutrients, FoodServingSize, MealType
    from loseit_client.client import LoggedEntry

    catalog_food = Food(
        unique_id=b"\xcc" * 16,
        name="Carrot, Whole",
        brand_name="",
        category="Carrot",
        language_tag="en-US",
        nutrients=FoodNutrients(base_units=61, calories=25, fat=0, carbohydrates=5.8, protein=0.1),
        servings=[FoodServingSize(size=61, measure_id=8, measure_singular="Gram", measure_plural="Grams")],
    )
    fake_loseit_client.search_catalog.return_value = [catalog_food]
    fake_loseit_client.database.return_value.get_food_by_uuid.return_value = None
    fake_loseit_client.log_food_from_catalog.return_value = LoggedEntry(
        entry_uuid=b"\x77" * 16,
        food_uuid=catalog_food.unique_id,
        name="Carrot, Whole",
        calories=50,
        meal=MealType.LUNCH,
        server_ack_txn_ids=[1],
        raw_response_fields=[1],
    )

    client, token = authed
    session = _initialize(client, token)

    r = _call_tool(client, token, session, "search_catalog", {"query": "carrot"}, request_id=20)
    assert r.status_code == 200

    r = _call_tool(
        client, token, session,
        "log_food",
        {"food_uuid": (b"\xcc" * 16).hex(), "meal": "lunch", "servings": 2},
        request_id=21,
    )
    assert r.status_code == 200, r.text
    body = _extract_sse_json(r.text)
    assert body["result"].get("isError") is not True, body

    call = fake_loseit_client.log_food_from_catalog.call_args
    assert call.kwargs["servings"] == 2
    assert call.kwargs["quantity"] is None  # explicitly NOT passed
    assert call.kwargs["meal"] == MealType.LUNCH


def test_log_food_servings_and_quantity_mutually_exclusive(authed, fake_loseit_client):
    """Passing both should produce a clear error, not a silent miscompute."""
    fake_loseit_client.database.return_value.get_food_by_uuid.return_value = None
    client, token = authed
    session = _initialize(client, token)

    r = _call_tool(
        client, token, session,
        "log_food",
        {
            "food_uuid": "00" * 16,
            "meal": "snacks",
            "servings": 2,
            "quantity": 122,
        },
    )
    assert r.status_code == 200
    body = _extract_sse_json(r.text)
    assert body["result"].get("isError") is True
    text = body["result"]["content"][0]["text"]
    assert "servings" in text and "quantity" in text

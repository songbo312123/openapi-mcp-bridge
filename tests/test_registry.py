import os
from types import SimpleNamespace

import pytest

from openapi_bridge.registry import ApiRegistry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS = os.path.join(ROOT, "specs")
OVERRIDES = os.path.join(ROOT, "overrides.yaml")


class FakeResponse:
    status_code = 200
    text = '{"ok": true}'

    def json(self):
        return {"ok": True}


class FakeClient:
    """Records what registry.call_api decided to send."""

    def __init__(self):
        self.calls = []

    def call(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        return FakeResponse()


@pytest.fixture()
def registry():
    return ApiRegistry(
        specs_dir=SPECS,
        overrides_path=OVERRIDES,
        default_service="notes",
    )


def _find(registry, name, project=""):
    spec = registry._find(name, project)
    assert spec is not None, f"{name} did not resolve"
    return spec


# ---- loading -----------------------------------------------------------

def test_loads_both_corpora(registry):
    projects = {a.project for a in registry.apis}
    assert projects == {"petstore", "notes"}


def test_service_routing_comes_from_extension_fields(registry):
    assert _find(registry, "/store/inventory").service == "petstore"
    assert _find(registry, "GET /notes").service == "notes"


def test_risk_defaults_follow_transport(registry):
    assert _find(registry, "/store/inventory").risk == "read"
    assert _find(registry, "POST /notes").risk == "write"


def test_explicit_risk_marks_dangerous(registry):
    assert _find(registry, "DELETE /notes/{noteId}").risk == "dangerous"


# ---- overrides ---------------------------------------------------------

def test_override_rewrites_method(registry):
    spec = _find(registry, "/notes/legacy")
    assert spec.method == "POST", "export says GET; handler accepts POST"


def test_override_rewrites_risk(registry):
    spec = _find(registry, "POST /notes/search")
    assert spec.risk == "read", "read-only POST must not demand confirmation"


def test_override_count_is_reported(registry):
    assert registry.stats()["overrides_applied"] == 2


# ---- retrieval ---------------------------------------------------------

def test_keyword_search_is_case_insensitive(registry):
    rows = registry.list_apis(keyword="NOTE")
    assert rows
    assert all("note" in (r["path"] + r["name"]).lower() for r in rows)


def test_project_filter_narrows_scope(registry):
    rows = registry.list_apis(project="petstore")
    assert rows and all(r["project"] == "petstore" for r in rows)


def test_risk_filter(registry):
    rows = registry.list_apis(risk="dangerous")
    assert {r["path"] for r in rows} == {"/notes/{noteId}", "/pet/{petId}"}


def test_limit_and_offset_paginate(registry):
    everything = registry.list_apis()
    page = registry.list_apis(limit=3, offset=2)
    assert page == everything[2:5]


def test_describe_returns_full_signature(registry):
    described = registry.describe_api("POST /notes")
    assert described["method"] == "POST"
    assert set(described["body_params"]) == {"title", "body"}
    assert described["content_type"] == "json"


def test_describe_is_ambiguous_rather_than_wrong(registry):
    """A name matching several endpoints must not silently pick one."""
    result = registry.describe_api("notes")
    assert "error" in result
    assert result["candidates"]


def test_cross_project_path_needs_disambiguation(registry):
    registry.apis.append(
        type(registry.apis[0])(
            name="dup", method="GET", path="/notes", service="petstore",
            project="petstore",
        )
    )
    result = registry.describe_api("GET /notes")
    assert "error" in result
    resolved = registry.describe_api("notes::GET /notes")
    assert resolved["method"] == "GET"


def test_concrete_url_resolves_against_templated_path(registry):
    """People and models say 'GET /notes/7', not 'GET /notes/{noteId}'."""
    spec = _find(registry, "GET /notes/7")
    assert spec.path == "/notes/{noteId}"

    # The bare path is genuinely ambiguous — GET/PUT/DELETE share the
    # template — so it must come back as candidates, not a guess.
    ambiguous = registry.describe_api("/notes/7")
    assert "error" in ambiguous
    assert {c["method"] for c in ambiguous["candidates"]} == {"GET", "PUT", "DELETE"}


# ---- calling -----------------------------------------------------------

def test_params_split_into_path_and_body(registry):
    client = FakeClient()
    registry.call_api(
        "PUT /notes/{noteId}",
        params={"noteId": 7, "title": "x", "body": "y"},
        client=client,
        confirm=True,
    )
    sent = client.calls[0]
    assert sent["path_params"] == {"noteId": 7}
    assert sent["params"] == {"title": "x", "body": "y"}


def test_get_params_go_to_query_not_body(registry):
    client = FakeClient()
    registry.call_api(
        "/pet/findByStatus",
        params={"status": "available"},
        client=client,
    )
    sent = client.calls[0]
    assert sent["query"] == {"status": "available"}
    assert sent["params"] == {}


def test_dangerous_requires_confirm(registry):
    client = FakeClient()
    result = registry.call_api("DELETE /notes/{noteId}", params={"noteId": 1},
                               client=client)
    assert result["need_confirm"] is True
    assert client.calls == [], "nothing may be sent without confirm"


def test_confirmed_dangerous_call_goes_through(registry):
    client = FakeClient()
    result = registry.call_api(
        "DELETE /notes/{noteId}", params={"noteId": 1},
        client=client, confirm=True,
    )
    assert result["status"] == 200
    assert client.calls[0]["confirm"] is True


def test_read_classified_post_does_not_demand_confirmation(registry):
    """A read-only POST must not be gated — otherwise confirming becomes reflex."""
    client = FakeClient()
    result = registry.call_api("POST /notes/search", params={"q": "x"}, client=client)
    assert result["status"] == 200
    assert client.calls, "the call should have gone out"
    assert client.calls[0]["confirm"] is True


def test_unknown_endpoint_returns_candidates(registry):
    result = registry.call_api("does-not-exist", client=FakeClient())
    assert "error" in result


def test_concrete_url_values_reach_the_call(registry):
    """'DELETE /notes/7' must not then ask the caller for noteId."""
    client = FakeClient()
    registry.call_api("DELETE /notes/7", client=client, confirm=True)
    assert client.calls[0]["path_params"] == {"noteId": "7"}

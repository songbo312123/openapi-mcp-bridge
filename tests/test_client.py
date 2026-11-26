from types import SimpleNamespace

import pytest

from openapi_bridge.client import AuthProvider, HttpClient, StaticTokenAuth
from openapi_bridge.redact import mask, sanitize


class FakeSession:
    def __init__(self):
        self.captured = None

    def request(self, method, url, **kwargs):
        self.captured = SimpleNamespace(method=method, url=url, kwargs=kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {}, text="")


class SigningAuth(AuthProvider):
    def headers(self):
        return {"X-App-Id": "demo"}

    def sign(self, params):
        return {"sig": "signed:" + str(len(params))}


@pytest.fixture()
def client():
    c = HttpClient(services={"api": "https://api.example.com"}, default_service="api")
    c.session = FakeSession()
    return c


# ---- redaction ---------------------------------------------------------

def test_sanitize_masks_known_keys():
    out = sanitize({"token": "abcdef123456", "name": "visible"})
    assert out["token"] == "abc***56"
    assert out["name"] == "visible"


def test_sanitize_is_recursive_and_does_not_mutate():
    original = {"data": {"password": "hunter2hunter2"}}
    out = sanitize(original)
    assert out["data"]["password"] == "hun***r2"
    assert original["data"]["password"] == "hunter2hunter2"


def test_short_values_are_fully_masked():
    assert mask("abc") == "***"
    assert mask("") == ""


# ---- path handling -----------------------------------------------------

def test_fill_path_uses_explicit_values():
    assert HttpClient.fill_path("/a/{id}", {}, {"id": 9}) == "/a/9"


def test_fill_path_falls_back_to_body_and_removes_it():
    body = {"id": 9, "name": "x"}
    assert HttpClient.fill_path("/a/{id}", body) == "/a/9"
    assert body == {"name": "x"}, "path params must not also be sent as fields"


def test_missing_path_param_is_loud():
    with pytest.raises(ValueError, match="missing path parameter"):
        HttpClient.fill_path("/a/{id}", {})


# ---- write guard -------------------------------------------------------

def test_write_without_confirm_is_refused(client):
    with pytest.raises(PermissionError):
        client.call("POST", "/notes", params={"a": 1})


def test_read_needs_no_confirm(client):
    client.call("GET", "/notes")
    assert client.session.captured.method == "GET"


def test_confirm_can_be_switched_off_for_read_only_use():
    c = HttpClient(services={"api": "https://x"}, confirm_writes=False)
    c.session = FakeSession()
    c.call("POST", "/notes")  # no exception


# ---- auth & signing ----------------------------------------------------

def test_auth_headers_and_signature_are_merged(client):
    client.auth = SigningAuth()
    client.call("POST", "/notes", params={"a": 1}, confirm=True)
    captured = client.session.captured
    assert captured.kwargs["headers"]["X-App-Id"] == "demo"
    assert captured.kwargs["data"]["sig"].startswith("signed:")


def test_bearer_token_reaches_the_header(client):
    client.auth = StaticTokenAuth("tkn123")
    client.call("GET", "/notes")
    assert client.session.captured.kwargs["headers"]["Authorization"] == "Bearer tkn123"


def test_signature_sees_body_and_query_together(client):
    client.auth = SigningAuth()
    client.call("POST", "/notes", params={"a": 1}, query={"b": 2}, confirm=True)
    assert client.session.captured.kwargs["data"]["sig"] == "signed:2"


# ---- transport ---------------------------------------------------------

def test_json_content_type_sends_json(client):
    client.call("POST", "/notes", params={"a": 1}, content_type="json", confirm=True)
    captured = client.session.captured
    assert captured.kwargs["json"] == {"a": 1}
    assert captured.kwargs["headers"]["Content-Type"] == "application/json"


def test_url_is_built_from_the_selected_service(client):
    client.call("GET", "/notes/1")
    assert client.session.captured.url == "https://api.example.com/notes/1"


def test_unknown_service_is_loud(client):
    with pytest.raises(ValueError, match="unknown service"):
        client.call("GET", "/notes", service="nope")

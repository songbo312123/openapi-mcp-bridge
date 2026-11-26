"""MCP server (stdio) over an OpenAPI corpus.

Four tools, deliberately. A small tool surface is what keeps selection
reliable — the model picks *an action*, then narrows *which endpoint* by
searching. Registering one tool per endpoint would move that cost back onto
the model.

Run::

    python -m openapi_bridge.mcp_server

Session state (a token, once set) lives in this process only. Nothing is
written to disk.
"""

import logging
import os
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from .client import HttpClient, StaticTokenAuth
from .registry import ApiRegistry
from . import config as config_mod

logging.basicConfig(level=logging.WARNING)

mcp = FastMCP("openapi-bridge")

_cfg = config_mod.load()
_registry = ApiRegistry(
    specs_dir=_cfg.get("specs_dir") or "specs",
    default_service=_cfg.get("default_service") or "",
    overrides_path=_cfg.get("overrides"),
)
_client = HttpClient(
    services=_cfg.get("services") or {},
    default_service=_cfg.get("default_service") or "",
    debug=bool(_cfg.get("debug")),
)


@mcp.tool()
def api_stats() -> Dict[str, Any]:
    """Corpus overview: totals, and the distribution across project / service
    / risk / auth, plus how many empirical overrides are in effect.

    Call this first when you do not know what the corpus contains.
    """
    return _registry.stats()


@mcp.tool()
def list_apis(
    keyword: str = "",
    project: str = "",
    service: str = "",
    risk: str = "",
    limit: int = 30,
    offset: int = 0,
) -> Dict[str, Any]:
    """Search the endpoint index. Returns a short list, not the whole corpus.

    keyword: matches name / path / summary / description / tags, case-insensitive.
    project / service / risk: narrow the scope (risk is read | write | dangerous).
    limit: default 30; raise it only when you genuinely need a wider sweep.

    Typical flow: list_apis("bookshelf") -> describe_api(best hit) -> call_api.
    """
    rows = _registry.list_apis(
        keyword=keyword, project=project, service=service,
        risk=risk, limit=limit, offset=offset,
    )
    total = len(_registry.list_apis(
        keyword=keyword, project=project, service=service, risk=risk,
    ))
    return {
        "total": total,
        "returned": len(rows),
        "offset": offset,
        "items": rows,
        "hint": (
            "Too many results: add keyword/project/risk. "
            "To call one unambiguously use 'project::METHOD /path'."
        ),
    }


@mcp.tool()
def describe_api(name: str, project: str = "") -> Dict[str, Any]:
    """Full signature for one endpoint: path/query/body/header params,
    auth scheme, risk level, owning service.

    name accepts a summary, 'METHOD /path', '/path', or 'project::METHOD /path'.
    If the name is ambiguous you get candidates back instead of a guess —
    calling the wrong endpoint is worse than asking again.
    """
    return _registry.describe_api(name, project=project)


@mcp.tool()
def login(token: str = "") -> Dict[str, Any]:
    """Attach a bearer token to subsequent calls. Stored in memory only.

    Omit the argument to pick the token up from BRIDGE_TOKEN instead, so it
    never has to appear in the conversation.
    """
    value = token or os.environ.get("BRIDGE_TOKEN", "")
    if not value:
        return {"ok": False, "error": "no token supplied and BRIDGE_TOKEN is unset"}
    _client.auth = StaticTokenAuth(value)
    return {"ok": True, "token_attached": True}


@mcp.tool()
def call_api(
    name: str,
    params: Optional[Dict[str, Any]] = None,
    project: str = "",
    confirm: bool = False,
) -> Dict[str, Any]:
    """Call an endpoint, routed to the right service automatically.

    name: as accepted by describe_api. Use 'project::METHOD /path' when the
    same path exists in more than one project.
    params: business parameters. path / query / body slots are split for you
    according to the spec — do not pre-format them.
    confirm: required for write operations and for anything classified
    'dangerous' (delete / export / payment).

    Returns {status, data, api}, or {error, need_confirm} when a guard fired.
    """
    return _registry.call_api(
        name, params=params or {}, client=_client,
        project=project, confirm=confirm,
    )


if __name__ == "__main__":
    mcp.run()

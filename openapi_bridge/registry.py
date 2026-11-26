"""Retrieval-first access to an OpenAPI corpus.

The problem this file exists to solve
-------------------------------------
An LLM can hold maybe a few dozen tool definitions before tool selection
starts to degrade and the prompt gets expensive. Real API suites have
hundreds or thousands of endpoints. Registering them all as tools does not
scale, and neither does pasting a spec into the prompt.

So we invert it. Instead of *registering* endpoints, we *index* them and give
the model three moves::

    list_apis(keyword)      -> a short, filtered list
    describe_api(name)      -> full signatures for the one you picked
    call_api(name, params)  -> actually call it

The model only ever carries the endpoints relevant to the task in front of
it. The size of the corpus stops mattering.
"""

import glob
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from . import overrides as overrides_mod
from .client import SAFE_METHODS, AuthProvider, HttpClient

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
PATH_PARAM_RE = re.compile(r"\{([^{}/]+)\}")

DEFAULT_SPECS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "specs")


def _template_regex(template: str, capture: bool = False) -> "re.Pattern[str]":
    """Turn ``/notes/{noteId}`` into a regex that also matches ``/notes/1``.

    Callers — especially a model — naturally refer to a concrete URL, not the
    template. Without this, "GET /notes/7" would fail to resolve even though
    the endpoint is right there in the index. With ``capture=True`` the
    placeholder values come back in the match groups, so they can be fed
    straight into the call instead of being asked for again.
    """
    parts = re.split(r"(\{[^{}/]+\})", template)
    pieces = []
    for part in parts:
        if part.startswith("{"):
            pieces.append("([^/]+)" if capture else "[^/]+")
        else:
            pieces.append(re.escape(part))
    return re.compile("^" + "".join(pieces) + "$")


@dataclass
class ApiSpec:
    """One operation, normalized out of an OpenAPI document.

    ``x-service`` / ``x-auth`` / ``x-risk`` / ``x-content-type`` are custom
    extensions. They exist because a single OpenAPI file can describe
    endpoints that live on different hosts and have different security
    models — something the spec format has no notion of.
    """

    name: str
    method: str
    path: str
    service: str = ""
    project: str = ""
    summary: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    auth: str = "none"
    risk: str = "unknown"
    verified: bool = False
    content_type: str = "form"
    path_params: Dict[str, Any] = field(default_factory=dict)
    query_params: Dict[str, Any] = field(default_factory=dict)
    body_params: Dict[str, Any] = field(default_factory=dict)
    header_params: Dict[str, Any] = field(default_factory=dict)
    deprecated: bool = False

    @property
    def key(self) -> str:
        """``METHOD path`` — summaries collide, this does not."""
        return f"{self.method} {self.path}"

    @property
    def full_key(self) -> str:
        return f"{self.project}::{self.key}" if self.project else self.key

    def brief(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "service": self.service,
            "project": self.project,
            "summary": self.summary,
            "risk": self.risk,
            "auth": self.auth,
        }


class ApiRegistry:
    def __init__(
        self,
        specs_dir: str = DEFAULT_SPECS_DIR,
        default_service: str = "",
        overrides_path: Optional[str] = None,
        default_auth: str = "none",
    ):
        self.specs_dir = specs_dir
        self.default_service = default_service
        self.default_auth = default_auth
        self.overrides = overrides_mod.load(overrides_path)
        self.apis: List[ApiSpec] = []
        self._load()

    # ---- loading -------------------------------------------------------

    def _load(self) -> None:
        patterns = ("*.yaml", "*.yml", "*.json")
        files: List[str] = []
        for pattern in patterns:
            files.extend(
                glob.glob(os.path.join(self.specs_dir, "**", pattern), recursive=True)
            )
        for path in sorted(files):
            with open(path, "r", encoding="utf-8") as fh:
                if path.endswith(".json"):
                    doc = json.load(fh)
                else:
                    doc = yaml.safe_load(fh)
            if isinstance(doc, dict) and doc.get("paths"):
                self._parse_doc(doc)

    def _parse_doc(self, doc: Dict[str, Any]) -> None:
        paths = doc.get("paths") or {}
        doc_project = doc.get("x-project") or ""
        doc_service = doc.get("x-service") or ""
        doc_auth = doc.get("x-auth") or ""

        for path, ops in paths.items():
            if not isinstance(ops, dict):
                continue
            path_service = ops.get("x-service") or doc_service or self.default_service
            for method, op in ops.items():
                if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                    continue

                service = op.get("x-service") or path_service
                auth = (
                    op.get("x-auth")
                    or doc_auth
                    or (self.default_auth if service else "unknown")
                )
                risk = op.get("x-risk") or (
                    "read" if method.upper() in SAFE_METHODS else "write"
                )
                content_type, body_params = self._extract_body(op)

                spec = ApiSpec(
                    name=op.get("summary") or f"{method.upper()} {path}",
                    method=method.upper(),
                    path=path,
                    service=service,
                    project=op.get("x-project") or doc_project,
                    summary=op.get("summary") or "",
                    description=op.get("description") or "",
                    tags=op.get("tags") or [],
                    auth=auth,
                    risk=risk,
                    verified=bool(op.get("x-verified")),
                    content_type=op.get("x-content-type") or content_type,
                    path_params=self._extract_path(path, op),
                    query_params=self._extract_query(op),
                    body_params=body_params,
                    header_params=self._extract_headers(op),
                    deprecated=bool(op.get("deprecated")),
                )
                overrides_mod.apply(spec, self.overrides)
                self.apis.append(spec)

    @staticmethod
    def _example_value(raw: Any) -> Any:
        """Turn an OpenAPI parameter/property object into a usable example.

        ``requestBody.properties`` holds *schemas*, not values. Passing one
        straight through puts a dict where a string belongs and the server
        rejects the call with a generic "bad parameter" — a confusing failure
        that looks like a routing bug.
        """
        if not isinstance(raw, dict):
            return raw
        example = raw.get("example", raw.get("default", ""))
        if example not in (None, ""):
            return example
        kind = raw.get("type")
        if kind in ("integer", "number"):
            return 0
        if kind == "boolean":
            return False
        return ""

    @classmethod
    def _extract_path(cls, path: str, op: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for param in op.get("parameters") or []:
            if param.get("in") == "path":
                raw = param.get("example")
                if raw in (None, ""):
                    raw = param.get("schema") or param
                out[param["name"]] = cls._example_value(raw)
        for name in PATH_PARAM_RE.findall(path):
            out.setdefault(name, "")
        return out

    @classmethod
    def _extract_query(cls, op: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for param in op.get("parameters") or []:
            if param.get("in") != "query":
                continue
            raw = param.get("example")
            if raw in (None, ""):
                raw = param.get("schema") or param
            out[param["name"]] = cls._example_value(raw)
        return out

    @classmethod
    def _extract_headers(cls, op: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for param in op.get("parameters") or []:
            if param.get("in") == "header":
                out[param["name"]] = cls._example_value(param)
        return out

    @classmethod
    def _extract_body(cls, op: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        content_type = "form"
        props: Dict[str, Any] = {}

        content = (op.get("requestBody") or {}).get("content") or {}
        for media, media_obj in content.items():
            schema = (media_obj or {}).get("schema") or {}
            props = schema.get("properties") or {}
            if "json" in str(media).lower():
                content_type = "json"
            break

        # Swagger 2 style: parameters with in=formData/body, or field lists.
        if not props:
            for param in op.get("parameters") or []:
                if param.get("fields"):
                    for f in param["fields"]:
                        props[f.get("name")] = f.get("example", "")
                elif param.get("in") in {"formData", "body"} and param.get("name"):
                    props[param["name"]] = param.get("example", "")

        return content_type, {k: cls._example_value(v) for k, v in props.items()}

    # ---- retrieval -----------------------------------------------------

    def list_apis(
        self,
        keyword: str = "",
        project: str = "",
        service: str = "",
        risk: str = "",
        tag: str = "",
        limit: int = 0,
        offset: int = 0,
        include_deprecated: bool = False,
    ) -> List[Dict[str, Any]]:
        needle = (keyword or "").strip().lower()
        rows: List[Dict[str, Any]] = []

        for api in self.apis:
            if not include_deprecated and api.deprecated:
                continue
            if project and api.project != project:
                continue
            if service and api.service != service:
                continue
            if risk and api.risk != risk:
                continue
            if tag and tag.lower() not in [t.lower() for t in api.tags]:
                continue
            if needle:
                haystack = " ".join([
                    api.name, api.path, api.summary, api.description,
                    " ".join(api.tags), api.project, api.service,
                ]).lower()
                if needle not in haystack:
                    continue
            rows.append(api.brief())

        if offset:
            rows = rows[offset:]
        if limit and limit > 0:
            rows = rows[:limit]
        return rows

    def stats(self) -> Dict[str, Any]:
        return {
            "total": len(self.apis),
            "deprecated": sum(1 for a in self.apis if a.deprecated),
            "verified": sum(1 for a in self.apis if a.verified),
            "by_project": dict(Counter(a.project or "(none)" for a in self.apis).most_common()),
            "by_service": dict(Counter(a.service or "(none)" for a in self.apis).most_common()),
            "by_risk": dict(Counter(a.risk for a in self.apis).most_common()),
            "by_auth": dict(Counter(a.auth for a in self.apis).most_common()),
            "overrides_applied": len(self.overrides),
        }

    def _candidates(self, name: str, project: str = "") -> List[ApiSpec]:
        scope = [a for a in self.apis if a.project == project] if project else self.apis
        if not scope:
            scope = self.apis

        exact = [a for a in scope if name in {a.full_key, a.key, a.path, a.name}]
        if exact:
            return exact

        # A concrete URL against a templated spec: "GET /notes/7" -> /notes/{noteId}
        templated = []
        for api in scope:
            if name.startswith(api.method + " "):
                candidate = name[len(api.method) + 1:]
            elif name.startswith("/"):
                candidate = name
            else:
                continue
            if _template_regex(api.path).match(candidate):
                templated.append(api)
        if templated:
            return templated

        if "::" in name:
            proj, rest = name.split("::", 1)
            hits = [
                a for a in self.apis
                if a.project == proj and rest in {a.key, a.path, a.name}
            ]
            if hits:
                return hits

        needle = name.lower()
        return [a for a in scope if needle in a.name.lower() or needle in a.path.lower()]

    def _find(self, name: str, project: str = "") -> Optional[ApiSpec]:
        """Resolve a name to exactly one spec, or None.

        Ambiguity is surfaced rather than guessed: a wrong guess here means
        calling the wrong endpoint, which is much worse than a retry.
        """
        hits = self._candidates(name, project)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            exact = [a for a in hits if a.name == name]
            if len(exact) == 1:
                return exact[0]
        return None

    def _resolve(
        self, name: str, project: str = ""
    ) -> Tuple[Optional[ApiSpec], Dict[str, Any]]:
        """Like :meth:`_find`, but also harvest literal path values.

        Resolving ``DELETE /notes/7`` gives the ``/notes/{noteId}`` spec *and*
        ``{"noteId": "7"}``, so the caller does not have to repeat what it
        already typed in the URL.
        """
        spec = self._find(name, project)
        if spec is None:
            return None, {}

        if name.startswith(spec.method + " "):
            candidate = name[len(spec.method) + 1:]
        elif name.startswith("/"):
            candidate = name
        else:
            return spec, {}

        match = _template_regex(spec.path, capture=True).match(candidate)
        if not match:
            return spec, {}
        names = PATH_PARAM_RE.findall(spec.path)
        return spec, dict(zip(names, match.groups()))

    def describe_api(self, name: str, project: str = "") -> Dict[str, Any]:
        api = self._find(name, project)
        if api is None:
            hits = self._candidates(name, project)
            return {
                "error": (
                    f"no unique endpoint for {name!r}; search with list_apis, or "
                    "disambiguate with 'project::METHOD /path'"
                ),
                "candidates": [h.brief() for h in hits[:10]],
            }
        return {
            "name": api.name,
            "project": api.project,
            "method": api.method,
            "path": api.path,
            "service": api.service,
            "auth": api.auth,
            "risk": api.risk,
            "verified": api.verified,
            "deprecated": api.deprecated,
            "summary": api.summary,
            "description": api.description,
            "tags": api.tags,
            "content_type": api.content_type,
            "path_params": api.path_params,
            "query_params": api.query_params,
            "body_params": api.body_params,
            "header_params": api.header_params,
        }

    # ---- calling -------------------------------------------------------

    @staticmethod
    def _split_params(
        api: ApiSpec, params: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        params = dict(params or {})
        path_values: Dict[str, Any] = {}
        for key in api.path_params:
            if key in params:
                path_values[key] = params.pop(key)

        query_values: Dict[str, Any] = {}
        for key in api.query_params:
            if key in params:
                query_values[key] = params.pop(key)

        if api.method in SAFE_METHODS:
            # Many exported specs mark GET params as formData. Sending them in
            # the body would silently drop them.
            query_values.update(params)
            params = {}

        return path_values, query_values, params

    def call_api(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        client: Optional[HttpClient] = None,
        project: str = "",
        service: str = "",
        confirm: bool = False,
    ) -> Dict[str, Any]:
        api, url_values = self._resolve(name, project)
        if api is None:
            hits = self._candidates(name, project)
            return {
                "error": f"no unique endpoint for {name!r}; search with list_apis",
                "candidates": [h.brief() for h in hits[:10]],
            }

        if api.deprecated:
            return {
                "error": f"{api.full_key} is marked deprecated and not callable",
                "api": api.brief(),
            }

        if api.risk == "dangerous" and not confirm:
            return {
                "error": (
                    f"{api.method} {api.path} is classified dangerous "
                    "(delete/export/payment); pass confirm=True"
                ),
                "need_confirm": True,
                "api": api.brief(),
            }

        if client is None:
            return {
                "error": "no HttpClient supplied",
                "api": api.brief(),
            }

        target_service = service or api.service
        merged = dict(url_values)
        merged.update(params or {})
        path_values, query_values, body = self._split_params(api, merged)

        # The risk classification is the authority, not the HTTP verb. A
        # read-only POST (search, preview, export-as-query) is extremely
        # common; forcing a confirmation on it trains operators to confirm
        # reflexively, which is how the guard stops working.
        send_confirm = bool(confirm) or api.risk == "read"

        try:
            response = client.call(
                api.method,
                api.path,
                params=body,
                query=query_values,
                path_params=path_values,
                service=target_service or None,
                content_type=api.content_type,
                confirm=send_confirm,
            )
        except PermissionError as exc:
            return {"error": str(exc), "need_confirm": True, "api": api.brief()}
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as data
            return {"error": f"{type(exc).__name__}: {exc}", "api": api.brief()}

        try:
            data = response.json()
        except ValueError:
            data = response.text[:1000]

        return {"status": response.status_code, "data": data, "api": api.brief()}


def build(
    specs_dir: str = DEFAULT_SPECS_DIR,
    overrides_path: Optional[str] = None,
    services: Optional[Dict[str, str]] = None,
    default_service: str = "",
    auth: Optional[AuthProvider] = None,
    debug: bool = False,
) -> Tuple[ApiRegistry, HttpClient]:
    """Wire a registry to a client. The usual entry point for scripts."""
    registry = ApiRegistry(
        specs_dir=specs_dir,
        default_service=default_service,
        overrides_path=overrides_path,
    )
    if services is None:
        services = {name: "http://127.0.0.1:18787" for name in
                    {a.service for a in registry.apis} - {""}}
    client = HttpClient(
        services=services,
        default_service=default_service,
        auth=auth,
        debug=debug,
    )
    return registry, client


def main() -> None:
    registry = ApiRegistry()
    stats = registry.stats()
    print("endpoints:", stats["total"])
    print("by project:", stats["by_project"])
    print("by service:", stats["by_service"])
    print("by risk:", stats["by_risk"])
    print("overrides:", stats["overrides_applied"])
    print()
    for row in registry.list_apis(limit=10):
        print(f"  [{row['risk']:9}] {row['method']:6} {row['path']:32} {row['summary']}")


if __name__ == "__main__":
    main()

"""Generic HTTP transport: multi-service routing, pluggable auth, write guard.

This layer knows nothing about any particular API. Everything
provider-specific — credentials, request signing, device fingerprints — is
supplied through an :class:`AuthProvider`.
"""

import logging
import re
from typing import Any, Dict, Optional

import requests

from .redact import sanitize

logger = logging.getLogger("openapi_bridge")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PATH_PARAM_RE = re.compile(r"\{([^{}/]+)\}")

DEFAULT_TIMEOUT = 15


class AuthProvider:
    """Hook for anything the provider needs on every request.

    Subclass and pass an instance to :class:`HttpClient`. Two seams:

    * :meth:`headers` — static-ish headers (e.g. a bearer token).
    * :meth:`sign` — return extra top-level params to merge into the signed
      payload. This is where a request signature goes.

    Keep both side-effect free: they may be called more than once per request
    during retries or debugging.
    """

    def headers(self) -> Dict[str, str]:
        return {}

    def sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {}


class StaticTokenAuth(AuthProvider):
    """Simplest useful provider: one bearer token, optional static extras."""

    def __init__(
        self,
        token: str,
        header: str = "Authorization",
        scheme: str = "Bearer",
        extras: Optional[Dict[str, str]] = None,
    ):
        self.token = token
        self.header = header
        self.scheme = scheme
        self.extras = dict(extras or {})

    def headers(self) -> Dict[str, str]:
        out = dict(self.extras)
        if self.token:
            value = f"{self.scheme} {self.token}".strip()
            out[self.header] = value
        return out


class NoAuth(AuthProvider):
    pass


class HttpClient:
    """One client, many services.

    ``services`` maps a logical service name to its base URL. Callers pick a
    service per request instead of juggling one client per host — which is
    what makes it practical to index hundreds of endpoints that live on a
    dozen different domains.
    """

    def __init__(
        self,
        services: Dict[str, str],
        default_service: str = "",
        auth: Optional[AuthProvider] = None,
        default_params: Optional[Dict[str, Any]] = None,
        confirm_writes: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
        verify_ssl: bool = True,
        debug: bool = False,
    ):
        if not services:
            raise ValueError("services must not be empty")
        if default_service and default_service not in services:
            raise ValueError(
                f"unknown default service {default_service!r}, "
                f"expected one of {sorted(services)}"
            )
        self.services = dict(services)
        self.default_service = default_service or next(iter(services))
        self.auth = auth or NoAuth()
        self.default_params = dict(default_params or {})
        self.confirm_writes = confirm_writes
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.debug = debug
        self.session = requests.Session()
        # System proxy settings silently break local bring-up more often than
        # they help. Opt in explicitly when going through a proxy.
        self.session.trust_env = False

    # ---- helpers -------------------------------------------------------

    def base_url(self, service: Optional[str] = None) -> str:
        name = service or self.default_service
        if name not in self.services:
            raise ValueError(
                f"unknown service {name!r}, expected one of {sorted(self.services)}"
            )
        return self.services[name].rstrip("/")

    @staticmethod
    def fill_path(
        path: str,
        body: Dict[str, Any],
        path_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Substitute ``{name}`` placeholders.

        Values come from ``path_params`` first, then from ``body`` (and are
        then removed from it, so they are not also sent as a form field).
        """
        values = dict(path_params or {})
        for name in PATH_PARAM_RE.findall(path):
            if name not in values and name in body:
                values[name] = body.pop(name)
            if name not in values:
                raise ValueError(f"missing path parameter {name!r} for {path}")
            path = path.replace("{" + name + "}", str(values[name]))
        return path

    # ---- the call ------------------------------------------------------

    def call(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        path_params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        service: Optional[str] = None,
        content_type: str = "form",
        confirm: bool = False,
    ) -> requests.Response:
        """Send one request.

        Slot semantics: ``params`` is the business payload (form body, or
        query string for GET); ``query`` is explicit query params even on
        POST; ``path_params`` fills the URL template.
        """
        method = method.upper()
        content_type = (content_type or "form").lower()

        # Write guard. Deliberately a hard failure rather than a prompt the
        # model can talk its way out of: prompts are advice, code is authority.
        if method not in SAFE_METHODS and self.confirm_writes and not confirm:
            raise PermissionError(
                f"{method} {path} writes data and needs explicit confirm=True"
            )

        body: Dict[str, Any] = dict(self.default_params)
        body.update(params or {})
        query_params: Dict[str, Any] = dict(query or {})

        path = self.fill_path(path, body, path_params)

        signed = {**body, **query_params}
        body.update(self.auth.sign(signed))

        req_headers: Dict[str, str] = {
            "Accept": "application/json",
            **self.auth.headers(),
        }
        req_headers["Content-Type"] = (
            "application/json"
            if content_type == "json"
            else "application/x-www-form-urlencoded"
        )
        if headers:
            req_headers.update(headers)

        url = self.base_url(service) + path

        if self.debug:
            logger.info(
                "-> %s %s body=%s query=%s",
                method, url, sanitize(body), sanitize(query_params),
            )

        if method in SAFE_METHODS:
            response = self.session.request(
                method, url,
                params={**body, **query_params},
                headers=req_headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        elif content_type == "json":
            response = self.session.request(
                method, url,
                params=query_params or None,
                json=body,
                headers=req_headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        else:
            response = self.session.request(
                method, url,
                params=query_params or None,
                data=body,
                headers=req_headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )

        if self.debug:
            try:
                payload = sanitize(response.json())
            except ValueError:
                payload = response.text[:200]
            logger.info("<- %s %s", response.status_code, payload)

        return response

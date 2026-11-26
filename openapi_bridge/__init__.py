"""Retrieval-first OpenAPI access for LLMs.

Three ideas, in order of importance:

1. One spec corpus is the single source of truth; everything else derives.
2. Indexing beats registering — the model searches for the endpoint it needs
   instead of being handed all of them.
3. Guardrails belong in code, not in prompts.
"""

from .client import AuthProvider, HttpClient, NoAuth, StaticTokenAuth
from .redact import sanitize
from .registry import ApiRegistry, ApiSpec, build

__all__ = [
    "ApiRegistry",
    "ApiSpec",
    "AuthProvider",
    "HttpClient",
    "NoAuth",
    "StaticTokenAuth",
    "build",
    "sanitize",
]

__version__ = "0.1.0"

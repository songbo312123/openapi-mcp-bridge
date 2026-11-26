"""Sensitive-value redaction for logs and error surfaces.

When an LLM is in the loop, every log line it can read is a potential data
leak: one unredacted credential and it lands in the conversation history.
Redaction therefore belongs in the transport layer, not in the caller.
"""

from typing import Any, Set

# Matched case-insensitively against dict keys.
SENSITIVE_KEYS: Set[str] = {
    "token", "access_token", "refresh_token", "id_token",
    "authorization", "auth", "zhauth", "cookie", "set-cookie",
    "password", "passwd", "pwd", "secret", "client_secret",
    "api_key", "apikey", "appkey", "signature", "sig",
    "session", "sessionid", "jsessionid", "credential",
    "phone", "mobile", "email", "idcard",
}


def mask(value: Any) -> str:
    """Keep a few leading/trailing characters so an operator can tell two
    tokens apart, without revealing either."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}***{text[-2:]}"


def sanitize(obj: Any, keys: Set[str] = SENSITIVE_KEYS) -> Any:
    """Return a redacted copy of ``obj``. The input is never mutated."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in keys:
                out[k] = mask(v)
            else:
                out[k] = sanitize(v, keys)
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize(x, keys) for x in obj]
    return obj

"""Config loading.

Keeping service URLs and credentials in a file rather than in code means
switching environments is a config change, not a code change — the thing
that most often blocks a tool from being handed to someone else.
"""

import os
from typing import Any, Dict, Optional

import yaml

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")


def load(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or os.environ.get("OPENAPI_BRIDGE_CONFIG") or DEFAULT_CONFIG
    if not os.path.exists(path):
        return {"services": {}, "default_service": "", "specs_dir": "specs"}

    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    services: Dict[str, str] = {}
    for name, cfg in (doc.get("services") or {}).items():
        if isinstance(cfg, str):
            services[name] = cfg
            continue
        base_url = os.environ.get(f"BRIDGE_{name.upper()}_BASE_URL") or cfg.get("base_url")
        if not base_url:
            raise ValueError(f"service {name!r} has no base_url")
        services[name] = base_url

    return {
        "services": services,
        "default_service": doc.get("default_service") or (next(iter(services), "")),
        "specs_dir": doc.get("specs_dir") or "specs",
        "overrides": doc.get("overrides"),
        "debug": bool(doc.get("debug")),
    }

"""Empirical corrections to a spec.

An OpenAPI document describes what someone *intended* an API to be. Reality
drifts: a path loses its prefix, a handler registers only POST while the doc
says GET, a route is commented out in source but still listed.

Fixing those in the exported document means editing a generated file, which
gets overwritten on the next export and quietly loses your corrections. So
corrections live here instead — one file, applied at load time, and every
entry carries a reason. Over time this file becomes the most valuable thing
in the repo: a record of how the system actually behaves.
"""

import os
from typing import Any, Dict, Optional, Tuple

import yaml

OverrideKey = Tuple[str, str, str]  # (project, METHOD, path)

ALLOWED_FIELDS = {
    "path", "method", "risk", "auth", "service", "content_type", "deprecated",
}


def load(path: Optional[str]) -> Dict[OverrideKey, Dict[str, Any]]:
    """Load ``overrides.yaml``.

    Expected shape::

        overrides:
          - project: petstore
            method: POST
            path: /pet/{petId}
            set:
              risk: dangerous
            reason: "Handler only registers POST; doc says GET."
    """
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    out: Dict[OverrideKey, Dict[str, Any]] = {}
    for i, item in enumerate(doc.get("overrides") or [], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"overrides[#{i}] must be a mapping")
        try:
            key = (
                str(item.get("project") or ""),
                str(item["method"]).upper(),
                str(item["path"]),
            )
            changes = dict(item.get("set") or {})
        except KeyError as exc:
            raise ValueError(f"overrides[#{i}] missing {exc}") from exc

        unknown = set(changes) - ALLOWED_FIELDS
        if unknown:
            raise ValueError(
                f"overrides[#{i}] sets unknown field(s) {sorted(unknown)}; "
                f"allowed: {sorted(ALLOWED_FIELDS)}"
            )
        if not item.get("reason"):
            raise ValueError(
                f"overrides[#{i}] has no reason; every correction must record why"
            )
        out[key] = changes
    return out


def apply(spec, overrides: Dict[OverrideKey, Dict[str, Any]]) -> None:
    """Apply matching corrections to ``spec`` in place.

    The lookup key uses the spec's *original* project/method/path, so an
    override that rewrites the path does not need to match its own output.
    """
    changes = overrides.get((spec.project, spec.method, spec.path))
    if not changes:
        return
    if "path" in changes:
        spec.path = changes["path"]
    if "method" in changes:
        spec.method = str(changes["method"]).upper()
    for field in ("risk", "auth", "service", "content_type"):
        if field in changes:
            setattr(spec, field, changes[field])
    if changes.get("deprecated"):
        spec.deprecated = True

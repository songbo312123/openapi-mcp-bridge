"""End-to-end walkthrough against the local mock API.

Start the mock first::

    python examples/mock_api.py        # terminal 1
    python examples/quickstart.py      # terminal 2

No network, no credentials.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openapi_bridge import config as config_mod  # noqa: E402
from openapi_bridge.client import HttpClient  # noqa: E402
from openapi_bridge.registry import ApiRegistry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def show(title, value):
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    cfg = config_mod.load(os.path.join(ROOT, "config.yaml"))
    registry = ApiRegistry(
        specs_dir=os.path.join(ROOT, cfg["specs_dir"]),
        default_service=cfg["default_service"],
        overrides_path=os.path.join(ROOT, cfg["overrides"]),
    )
    client = HttpClient(
        services=cfg["services"],
        default_service=cfg["default_service"],
        confirm_writes=True,
    )

    show("corpus", registry.stats())

    # 1. The model's first move: search, do not enumerate.
    show("list_apis(keyword='note')", registry.list_apis(keyword="note", limit=10))

    # 2. Narrow to one endpoint and read its signature.
    show("describe_api('POST /notes')", registry.describe_api("POST /notes"))

    # 3. Write something. Writes need an explicit confirm at both layers.
    created = registry.call_api(
        "POST /notes",
        params={"title": "hello", "body": "world"},
        client=client,
        confirm=True,
    )
    show("create a note", created)
    note_id = (created.get("data") or {}).get("id")

    show("read it back", registry.call_api(
        f"GET /notes/{note_id}", client=client,
    ))

    # 4. The empirical override in action. The exported spec says GET
    #    /notes/legacy; overrides.yaml corrects it to POST, because that is
    #    what the handler actually accepts.
    show("describe_api('/notes/legacy') — after override",
         registry.describe_api("/notes/legacy"))
    show("call the corrected route", registry.call_api(
        "/notes/legacy", params={"q": "hello"}, client=client,
    ))

    # 5. delete is classified dangerous: no confirm, no call.
    show("delete without confirm — blocked", registry.call_api(
        f"DELETE /notes/{note_id}", client=client,
    ))

    show("delete with confirm", registry.call_api(
        f"DELETE /notes/{note_id}", client=client, confirm=True,
    ))

    print("\nDone. The guard is code, not a prompt: nothing above depends on "
          "the model behaving.")


if __name__ == "__main__":
    main()

# openapi-mcp-bridge

**Give an LLM a 2,000-endpoint OpenAPI corpus without giving it 2,000 tools.**

[中文说明](README.zh-CN.md)

---

## The problem

Large internal platforms don't have ten endpoints. They have hundreds,
spread across a dozen services, half of them undocumented, and the export
you have is subtly wrong in maybe forty places.

Two things then fail:

- **People** can't hold the surface in their heads. Finding the right
  endpoint becomes archaeology.
- **Models** can't be handed it either. Register all endpoints as tools and
  selection quality collapses while prompt cost explodes. Paste the spec and
  you burn the context before the first question.

This project takes a third route: **index the corpus, hand the model three
moves, and let it search.**

```
list_apis(keyword)      → a short filtered list
describe_api(name)      → full signature for the one you picked
call_api(name, params)  → call it, routed and guarded
```

The model only ever carries the endpoints relevant to the task in front of
it. Corpus size stops being a constraint.

---

## Quickstart

Runs offline. No account, no credentials, no network.

```bash
pip install -e .            # requests + PyYAML
python examples/mock_api.py # terminal 1: a throwaway in-memory API
python examples/quickstart.py  # terminal 2
```

You'll see the corpus indexed, a note created, an override correcting a
stale route, and a `DELETE` refused because nobody confirmed it.

---

## How it works

```
       Claude Code / Desktop
                │  MCP (stdio)
                ▼
┌───────────────────────────────────┐
│  mcp_server.py     4 tools        │
├───────────────────────────────────┤
│  registry.py       search / describe / call
│                    + risk & auth metadata
│                    + empirical overrides
├───────────────────────────────────┤
│  client.py         multi-service routing
│                    pluggable auth + signing
│                    write guard, redacted logs
├───────────────────────────────────┤
│  specs/*.yaml      one corpus, the source of truth
└───────────────────────────────────┘
```

Everything derives from `specs/`. Adding an endpoint is a spec change, not a
code change.

---

## Design decisions

### 1. Retrieval beats registration

Registering one tool per endpoint moves the whole corpus into the prompt.
Searching moves only the answer. The tradeoff is one extra round trip; the
benefit is that the approach still works at ten thousand endpoints.

A useful side effect: retrieval failures are visible. If the model can't find
the endpoint, you see it, instead of it silently picking the second-best of
forty plausible tools.

### 2. Guardrails go in code, not in prompts

A prompt saying "ask before deleting" is advice. This is authority:

```python
if method not in SAFE_METHODS and self.confirm_writes and not confirm:
    raise PermissionError(f"{method} {path} writes data and needs explicit confirm=True")
```

Risk is classified per endpoint, not per HTTP verb. A read-only `POST`
(search, preview, report generation) is `read` and is *not* gated — because
forcing confirmation on routine reads teaches people to confirm reflexively,
and a reflexive guard is not a guard. `DELETE`, export and payment endpoints
are `dangerous` and are gated regardless of who asks.

### 3. Corrections live outside the export

Exported specs are generated artifacts. Edit them and your corrections
disappear on the next export. So discrepancies go in `overrides.yaml`,
keyed by the original `(project, method, path)`, and every entry carries a
`reason`:

```yaml
overrides:
  - project: notes
    method: GET
    path: /notes/legacy
    set:
      method: POST
    reason: >-
      The export still lists a GET, but the handler only registers POST.
```

Over a few months this file becomes the most valuable thing in the repo: a
record of where the documentation and the system disagree, and why. Without
a mandatory `reason`, it decays into a pile of unexplained patches.

### 4. Everything provider-specific is a hook

Service routing, credentials, and request signing vary wildly between
platforms. Rather than bake any of it in, `AuthProvider` exposes two seams:

```python
class MyAuth(AuthProvider):
    def headers(self):               return {"Authorization": f"Bearer {self.token}"}
    def sign(self, params):          return {"sig": compute_sig(params)}
```

The transport knows nothing about your signature scheme; your scheme knows
nothing about the transport.

---

## Use with Claude Code

The MCP server is an optional extra:

```bash
pip install -e ".[mcp]"
```

Add to your MCP config:

```json
{
  "mcpServers": {
    "openapi-bridge": {
      "command": "python3",
      "args": ["-m", "openapi_bridge.mcp_server"],
      "cwd": "/path/to/openapi-mcp-bridge"
    }
  }
}
```

Then, in plain language:

> "Search the API index for anything about notes, then create one called
> hello."

Tools exposed:

| tool | purpose |
|---|---|
| `api_stats` | corpus overview — call first when you know nothing |
| `list_apis` | filtered search, returns a page not the world |
| `describe_api` | full signature for one endpoint |
| `call_api` | invoke, with routing + guard |
| `login` | attach a bearer token (memory only) |

---

## Adding your own corpus

1. Export OpenAPI 3 from your gateway (or write it by hand).
2. Drop the file in `specs/`.
3. Annotate per-endpoint metadata:

   ```yaml
   x-project: billing          # logical project, used for disambiguation
   x-service: billing-api      # which host this hits
   x-auth: token               # none | token | ...
   x-risk: read                # read | write | dangerous
   x-content-type: json        # form | json
   ```

   `x-service` can sit on the document, on a path, or on a single operation.
   This is the one thing OpenAPI has no notion of: a single spec describing
   endpoints that live on different hosts.
4. Register the host in `config.yaml` under `services:`.
5. Run `python -m openapi_bridge.registry` to confirm the index picked it up.

---

## Status and limits

- 37 offline tests, no network required (`pytest -q`).
- The bundled corpus is Petstore plus a local mock — deliberately small, so
  every behaviour in the README can be verified in under a minute.
- Keyword search is substring matching. Past a few hundred endpoints per
  project you'll want embedding-based recall over `summary` first, then
  exact matching. That's the known next step, not a hidden gap.
- No caching, no retry/backoff, no response schema validation yet.

Measured on a real internal deployment: 2,023 endpoints across 12 services,
indexed in one process, with 40+ empirical overrides accumulated over four
months of use.

---

## License

MIT.

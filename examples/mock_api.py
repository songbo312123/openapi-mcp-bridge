"""A throwaway in-memory API that implements specs/notes.yaml.

Exists so the whole thing runs with no network, no account, no credentials.
Start it, run examples/quickstart.py in another shell, done.

    python examples/mock_api.py
"""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NOTES = {}
NEXT_ID = {"value": 1}

NOTE_PATH = re.compile(r"^/notes/(\d+)$")


def _handler_factory():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep stdout clean
            pass

        # ---- plumbing --------------------------------------------------
        def _send(self, status, payload=None):
            body = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return {}

        # ---- routes ----------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/notes":
                return self._send(200, {"items": list(NOTES.values())})
            match = NOTE_PATH.match(path)
            if match:
                note = NOTES.get(int(match.group(1)))
                if note is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, note)
            return self._send(404, {"error": f"no route for GET {path}"})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            payload = self._read_json()

            if path == "/notes":
                note = {
                    "id": NEXT_ID["value"],
                    "title": payload.get("title", ""),
                    "body": payload.get("body", ""),
                }
                NOTES[note["id"]] = note
                NEXT_ID["value"] += 1
                return self._send(201, note)

            if path in ("/notes/search", "/notes/legacy"):
                q = str(payload.get("q", "")).lower()
                hits = [
                    n for n in NOTES.values()
                    if q in n["title"].lower() or q in n["body"].lower()
                ]
                return self._send(200, {"q": q, "items": hits})

            return self._send(404, {"error": f"no route for POST {path}"})

        def do_PUT(self):
            match = NOTE_PATH.match(self.path.split("?", 1)[0])
            if not match:
                return self._send(404, {"error": f"no route for PUT {self.path}"})
            note_id = int(match.group(1))
            if note_id not in NOTES:
                return self._send(404, {"error": "not found"})
            payload = self._read_json()
            NOTES[note_id].update({
                "title": payload.get("title", NOTES[note_id]["title"]),
                "body": payload.get("body", NOTES[note_id]["body"]),
            })
            return self._send(200, NOTES[note_id])

        def do_DELETE(self):
            match = NOTE_PATH.match(self.path.split("?", 1)[0])
            if not match:
                return self._send(404, {"error": f"no route for DELETE {self.path}"})
            note_id = int(match.group(1))
            if NOTES.pop(note_id, None) is None:
                return self._send(404, {"error": "not found"})
            return self._send(204)

    return Handler


def main(host="127.0.0.1", port=18787):
    server = ThreadingHTTPServer((host, port), _handler_factory())
    print(f"mock notes API on http://{host}:{port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

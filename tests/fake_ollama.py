"""A local HTTP server that answers like Ollama, for tests: /api/tags lists `models`, /api/chat
answers `reply(body)`, /api/generate (unload) is recorded. `calls` keeps every /api/chat body."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def answer(kind="photo", description="Photo d'un préhenseur à ventouses sur un robot Delta.", visible="",
           keywords=("préhenseur", "ventouses", "gripper", "suction cups")):
    content = json.dumps({"type": kind, "description": description, "texte_visible": visible,
                          "mots_cles": list(keywords)}, ensure_ascii=False)
    return 200, {"model": "x", "message": {"role": "assistant", "content": content}, "done": True}


def default_reply(body):
    return answer()


class FakeOllama:
    def __init__(self, models=("test-vision",)):
        self.models = list(models)
        self.calls: list[dict] = []
        self.unloaded: list[str] = []
        self.reply = default_reply
        self.delay = 0.0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload):
                data = (payload if isinstance(payload, str) else json.dumps(payload)).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send(200, {"models": [{"name": m} for m in fake.models]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                if self.path == "/api/generate":
                    fake.unloaded.append(body.get("model"))
                    self._send(200, {"done": True})
                    return
                fake.calls.append(body)
                if fake.delay:
                    time.sleep(fake.delay)
                status, payload = fake.reply(body)
                try:
                    self._send(status, payload)
                except OSError:  # the client gave up (timeout test)
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cfdi.runner import notify_failure

received: list[dict] = []


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        received.append({"path": self.path, "json": json.loads(body)})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_notify_failure_posts_report_json():
    received.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ok = notify_failure(
            {"status": "aborted", "guide_id": "amorino-gelato", "ticket": {"folio": "369636"}},
            f"http://127.0.0.1:{server.server_address[1]}/hook",
        )
    finally:
        server.shutdown()

    assert ok is True
    assert len(received) == 1
    assert received[0]["path"] == "/hook"
    posted = received[0]["json"]
    occurred_at = posted.pop("occurred_at")
    assert posted == {
        "status": "aborted",
        "guide_id": "amorino-gelato",
        "ticket": {"folio": "369636"},
    }
    assert len(occurred_at) == 19  # YYYY-MM-DDTHH:MM:SS


def test_notify_failure_never_raises_on_dead_endpoint():
    ok = notify_failure({"status": "aborted"}, "http://127.0.0.1:1/nothing-here")
    assert ok is False

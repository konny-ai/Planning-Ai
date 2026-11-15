"""Real loopback HTTP transport tests, without external model credentials."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from planning_factory.adapters import decision_model as dm


class LocalModelTransportTests(unittest.TestCase):
    def setUp(self):
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(size))
                observed.append({"path": self.path, "payload": payload})
                if self.server.redirect:
                    self.send_response(302)
                    self.send_header("Location", "https://example.invalid/private")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = json.dumps({"choices": [{"message": {"content":
                    json.dumps({"label": "CAPACITY_REVIEW", "confidence": .93,
                                "reason": "Local simulated capacity review"})}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.redirect = False
        self.calls = observed
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def config(self):
        return {**dm.DEFAULT, "enabled": True, "provider": "local",
                "model": "simulated-local", "endpoint":
                    "http://127.0.0.1:" + str(self.server.server_port) + "/v1"}

    def test_real_local_http_request_and_structured_answer(self):
        summary = {"scheduled_count": 8, "unscheduled_count": 2, "delivery_count": 3}
        result = dm.evaluate(self.config(), summary, approved=True)
        self.assertEqual(result["status"], "shadow")
        self.assertEqual(result["label"], "CAPACITY_REVIEW")
        self.assertEqual(self.calls[0]["path"], "/v1/chat/completions")
        self.assertEqual(self.calls[0]["payload"]["model"], "simulated-local")
        self.assertIn('"unscheduled_count": 2',
                      self.calls[0]["payload"]["messages"][1]["content"])
        self.assertFalse(result["authorizes_execution"])

    def test_no_network_before_per_request_approval(self):
        result = dm.evaluate(self.config(), {}, approved=False)
        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(self.calls, [])

    def test_redirect_denied_and_fallback_never_executes(self):
        self.server.redirect = True
        result = dm.evaluate(self.config(), {"scheduled_count": 1}, approved=True)
        self.assertEqual(result["status"], "fallback")
        self.assertFalse(result["authorizes_execution"])
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()

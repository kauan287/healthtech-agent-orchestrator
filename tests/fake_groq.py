"""A local stand-in for Groq's chat completions endpoint.

ChatGroq reads GROQ_API_BASE, so tests point the real client and SDK at this server and
see the exact request each agent sends.
"""

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

Reply = tuple[int, dict[str, Any], dict[str, str]]
Responder = Callable[[dict[str, Any]], Reply]


def completion(content: str, input_tokens: int, output_tokens: int) -> Reply:
    body = {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }
    return 200, body, {}


def rate_limited(retry_after_s: str) -> Reply:
    error = {"error": {"message": "Rate limit reached", "type": "requests", "code": "rate_limit"}}
    return 429, error, {"retry-after": retry_after_s}


@dataclass
class FakeGroq:
    url: str
    requests: list[dict[str, Any]] = field(default_factory=list)


@contextmanager
def fake_groq(respond: Responder) -> Iterator[FakeGroq]:
    """Serve `respond` on localhost. The yielded object collects every request body."""
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            status, payload, headers = respond(body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield FakeGroq(url=f"http://127.0.0.1:{server.server_port}", requests=requests)
    finally:
        server.shutdown()
        server.server_close()

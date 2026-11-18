# -*- coding: utf-8 -*-
"""Kimi protocol regression: model parameters, streaming, and transient errors."""
from __future__ import annotations

import json
import os
import sys
import threading
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pdf_audit.providers import OpenAICompatibleVisionProvider, ProviderError


class Handler(BaseHTTPRequestHandler):
    payloads: list[dict] = []
    responses: list[tuple[int, dict]] = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.payloads.append(payload)
        status, result = self.responses.pop(0) if self.responses else (
            200, {"choices": [{"message": {"content": "{\"ok\":true}"}}]})
        if status == 200 and payload.get("stream"):
            chunks = [
                {"choices": [{"delta": {"reasoning_content": "thinking"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": '{"ok":'}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "true}"}, "finish_reason": "stop"}]},
            ]
            body = b"".join(b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n"
                            for chunk in chunks)
            if not result.get("incomplete"):
                body += b"data: [DONE]\n\n"
        else:
            body = json.dumps(result).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream" if status == 200 and payload.get("stream")
                         else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def call(provider: str, model: str, base: str, effort: str | None = None) -> dict:
    old = {k: os.environ.get(k) for k in
           ("PDF_AI_PROVIDER", "PDF_AI_BASE", "PDF_AI_MODEL", "PDF_AI_KEY")}
    try:
        os.environ["PDF_AI_PROVIDER"] = provider
        os.environ["PDF_AI_BASE"] = base
        os.environ["PDF_AI_MODEL"] = model
        os.environ["PDF_AI_KEY"] = "test"
        assert OpenAICompatibleVisionProvider(timeout=30).call_json(
            "test", "test", reasoning_effort=effort) == {"ok": True}
        return Handler.payloads[-1]
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d/v1" % server.server_port
    try:
        k26 = call("kimi", "kimi-k2.6", base)
        assert k26["temperature"] == 0.6
        assert k26["thinking"] == {"type": "disabled"}
        assert k26["max_completion_tokens"] == 16384
        k27 = call("custom", "kimi-k2.7-code", base)
        assert k27["temperature"] == 1 and "thinking" not in k27
        qwen = call("qwen", "qwen-vl-max", base)
        assert qwen["temperature"] == 0 and "thinking" not in qwen
        k3 = call("kimi", "kimi-k3", base)
        assert "temperature" not in k3 and k3["reasoning_effort"] == "low"
        assert k3["stream"] is True
        k31 = call("kimi", "kimi-k3.1-pro", base)
        assert "temperature" not in k31 and "thinking" not in k31
        assert k31["reasoning_effort"] == "high"
        assert k31["max_completion_tokens"] == 32768
        assert k31["stream"] is True
        assert call("kimi", "kimi-k3.1-pro", base, "low")["reasoning_effort"] == "low"
        Handler.responses = [(200, {"incomplete": True})]
        try:
            call("kimi", "kimi-k3.1-pro", base)
            assert False, "stream without DONE must not be accepted"
        except ProviderError as exc:
            assert "未收到完成标记" in str(exc)
        overloaded = (429, {"error": {"type": "engine_overloaded_error", "message": "try again later"}})
        with patch("pdf_audit.providers.time.sleep") as sleep:
            Handler.responses = [overloaded, overloaded]
            before = len(Handler.payloads)
            call("kimi", "kimi-k2.6", base)
            assert len(Handler.payloads) - before == 3
            assert sleep.call_count == 2
            Handler.responses = [overloaded] * 4
            before = len(Handler.payloads)
            try:
                call("kimi", "kimi-k2.6", base)
                assert False, "overload should fail after finite retries"
            except ProviderError as exc:
                assert "已自动重试 3 次" in str(exc)
            assert len(Handler.payloads) - before == 4
            Handler.responses = [(429, {"error": {"type": "insufficient_quota", "message": "quota exhausted"}})]
            before = len(Handler.payloads)
            try:
                call("kimi", "kimi-k2.6", base)
                assert False, "quota error should fail"
            except ProviderError as exc:
                assert "额度受限" in str(exc)
            assert len(Handler.payloads) - before == 1
        print("PASS provider temperature routing")
        print("PASS K3 streaming and incomplete stream rejection")
        print("PASS transient overload retry and quota handling")
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())

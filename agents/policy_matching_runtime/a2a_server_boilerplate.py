"""
A2A server boilerplate: HTTP handling, JSON-RPC envelope parsing/serialization,
/ping, and agent-card rendering — identical across every agent, so it's copied
into each Runtime folder rather than imported. Each server.py only needs to
define its AgentCard, implement handle_request(scoped_input), and call
run_server(card, description, handle_request) in __main__.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("a2a-server")


def agent_card_json(card: Any, runtime_url: str, description: str) -> dict[str, Any]:
    # `card` duck-types AgentCard (agent_name, capabilities, input_schema,
    # output_schema, version). A2A skills have no schema fields, so
    # input/output_schema are folded into each skill's description.
    skills = [
        {
            "id": capability,
            "name": capability,
            "description": (
                f"Input: {json.dumps(card.input_schema)}. Output: {json.dumps(card.output_schema)}."
            ),
            "tags": [],
        }
        for capability in card.capabilities
    ]
    return {
        "name": card.agent_name,
        "description": description,
        "version": card.version,
        "url": runtime_url,
        "protocolVersion": "0.3.0",
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": skills,
    }


def jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _to_jsonable(value: Any) -> Any:
    return asdict(value) if is_dataclass(value) else value


def make_handler(
    card: Any,
    description: str,
    handle_request: Callable[[dict[str, Any]], Any],
    validation_error_cls: type[Exception],
    agent_error_cls: type[Exception],
) -> type[BaseHTTPRequestHandler]:
    # validation_error_cls maps to JSON-RPC -32052/HTTP 400 (bad input);
    # agent_error_cls maps to -32603/HTTP 500 (real agent failure).

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/.well-known/agent-card.json":
                runtime_url = self.headers.get("AGENTCORE_RUNTIME_URL", "")
                self._send_json(200, agent_card_json(card, runtime_url, description))
            elif self.path == "/ping":
                self._send_json(200, {"status": "Healthy"})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/":
                self._send_json(404, {"error": "not found"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length) if content_length else b""

            rpc_id = None
            try:
                body = json.loads(raw_body)
                rpc_id = body.get("id")
                message = body["params"]["message"]
                input_text = message["parts"][0]["text"]
                scoped_input = json.loads(input_text)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                logger.warning("Validation error parsing request: %s", exc)
                self._send_json(
                    400,
                    jsonrpc_error(
                        rpc_id, -32052, "Validation error - Invalid request data"
                    ),
                )
                return

            try:
                result = handle_request(scoped_input)
            except validation_error_cls as exc:
                logger.warning("Validation error from handler: %s", exc)
                self._send_json(
                    400, jsonrpc_error(rpc_id, -32052, f"Validation error - {exc}")
                )
                return
            except agent_error_cls as exc:
                logger.warning("Agent failure: %s", exc)
                self._send_json(
                    500, jsonrpc_error(rpc_id, -32603, f"Internal error - {exc}")
                )
                return

            output_value = _to_jsonable(result)
            output_text = "null" if output_value is None else json.dumps(output_value)
            self._send_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "artifacts": [
                            {
                                "artifactId": str(uuid.uuid4()),
                                "name": "agent_response",
                                "parts": [{"kind": "text", "text": output_text}],
                            }
                        ]
                    },
                },
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.info("%s - %s", self.address_string(), format % args)

    return Handler


def run_server(
    card: Any,
    description: str,
    handle_request: Callable[[dict[str, Any]], Any],
    validation_error_cls: type[Exception] = ValueError,
    agent_error_cls: type[Exception] = Exception,
    port: int = 9000,
) -> None:
    handler_cls = make_handler(
        card, description, handle_request, validation_error_cls, agent_error_cls
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    logger.info("%s A2A server listening on 0.0.0.0:%d", card.agent_name, port)
    server.serve_forever()

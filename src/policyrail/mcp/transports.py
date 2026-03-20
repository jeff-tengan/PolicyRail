from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from .client import DEFAULT_MCP_PROTOCOL_VERSION, MCPTransportSessionExpired


class StdioMCPTransport:
    def __init__(
        self,
        command: list[str] | tuple[str, ...],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.command = list(command)
        self.cwd = str(cwd) if cwd is not None else None
        self.env = dict(env or {})
        self.timeout = timeout
        self._protocol_version = DEFAULT_MCP_PROTOCOL_VERSION
        self._next_id = 1
        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[str | None] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._response_buffer: dict[int, dict[str, Any]] = {}
        self._io_lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_process()
        is_notification = method.startswith("notifications/")

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        with self._io_lock:
            request_id: int | None = None
            if not is_notification:
                request_id = self._next_id
                self._next_id += 1
                payload["id"] = request_id

            self._write_payload(payload)

            if is_notification:
                return {}

            if request_id in self._response_buffer:
                return self._unwrap_response(self._response_buffer.pop(request_id))

            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timeout ao aguardar resposta MCP via stdio para '{method}'. "
                        f"Stderr recente: {self._stderr_preview()}"
                    )

                try:
                    raw_message = self._stdout_queue.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError(
                        f"Timeout ao aguardar resposta MCP via stdio para '{method}'. "
                        f"Stderr recente: {self._stderr_preview()}"
                    ) from exc

                if raw_message is None:
                    raise RuntimeError(
                        "Processo MCP via stdio encerrou antes de responder. "
                        f"Stderr recente: {self._stderr_preview()}"
                    )

                message = self._parse_message(raw_message)
                if not isinstance(message, dict):
                    continue

                message_id = message.get("id")
                if isinstance(message_id, int):
                    if message_id == request_id:
                        return self._unwrap_response(message)
                    self._response_buffer[message_id] = message

    def set_protocol_version(self, protocol_version: str) -> None:
        self._protocol_version = protocol_version

    def close(self) -> None:
        if self._process is None:
            return

        try:
            if self._process.stdin:
                self._process.stdin.close()
        except OSError:
            pass
        try:
            if self._process.stdout:
                self._process.stdout.close()
        except OSError:
            pass
        try:
            if self._process.stderr:
                self._process.stderr.close()
        except OSError:
            pass

        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)

        self._process = None

    def _ensure_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        env = os.environ.copy()
        env.update(self.env)
        self._stdout_queue = queue.Queue()
        self._stderr_tail.clear()
        self._response_buffer.clear()

        self._process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

        assert self._process.stdout is not None
        assert self._process.stderr is not None

        self._stdout_thread = threading.Thread(
            target=self._pump_stdout,
            args=(self._process.stdout,),
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr,
            args=(self._process.stderr,),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _write_payload(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Processo MCP via stdio nao esta disponivel.")

        self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def _pump_stdout(self, stream) -> None:
        for line in stream:
            self._stdout_queue.put(line)
        self._stdout_queue.put(None)

    def _pump_stderr(self, stream) -> None:
        for line in stream:
            compact = line.strip()
            if compact:
                self._stderr_tail.append(compact)

    @staticmethod
    def _parse_message(raw_message: str) -> dict[str, Any] | list[Any] | None:
        candidate = raw_message.strip()
        if not candidate:
            return None
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _unwrap_response(message: dict[str, Any]) -> dict[str, Any]:
        if "error" in message:
            error = message["error"]
            raise RuntimeError(f"Erro MCP via stdio: {error}")
        return _ensure_dict(message.get("result"))

    def _stderr_preview(self) -> str:
        if not self._stderr_tail:
            return "sem stderr"
        return " | ".join(self._stderr_tail)


class StreamableHTTPMCPTransport:
    def __init__(
        self,
        endpoint_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._next_id = 1
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._request_lock = threading.Lock()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        is_notification = method.startswith("notifications/")
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        request_id: int | None = None
        with self._request_lock:
            if not is_notification:
                request_id = self._next_id
                self._next_id += 1
                payload["id"] = request_id

            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **self.headers,
            }
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
            if self._protocol_version and method != "initialize":
                headers["MCP-Protocol-Version"] = self._protocol_version

            request = urllib.request.Request(
                self.endpoint_url,
                data=data,
                headers=headers,
                method="POST",
            )

            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    session_id = response.headers.get("Mcp-Session-Id")
                    if session_id:
                        self._session_id = session_id

                    body = response.read()
                    if is_notification or response.status in {202, 204}:
                        return {}

                    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 404 and self._session_id:
                    self._session_id = None
                    raise MCPTransportSessionExpired(
                        "Sessao MCP HTTP expirou; uma nova inicializacao sera necessaria."
                    ) from exc
                raise RuntimeError(f"Erro HTTP MCP {exc.code}: {body}") from exc

        if not body:
            return {}

        if content_type == "application/json":
            message = json.loads(body.decode("utf-8"))
            return self._extract_result_from_json(message, request_id)

        if content_type == "text/event-stream":
            return self._extract_result_from_sse(body.decode("utf-8"), request_id)

        try:
            message = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Resposta MCP HTTP com content-type nao suportado: {content_type or 'desconhecido'}"
            ) from exc
        return self._extract_result_from_json(message, request_id)

    def set_protocol_version(self, protocol_version: str) -> None:
        self._protocol_version = protocol_version

    def close(self) -> None:
        if not self._session_id:
            return

        headers = dict(self.headers)
        headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version

        request = urllib.request.Request(
            self.endpoint_url,
            headers=headers,
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                pass
        except Exception:
            pass
        finally:
            self._session_id = None

    @staticmethod
    def _extract_result_from_json(
        message: dict[str, Any] | list[Any],
        request_id: int | None,
    ) -> dict[str, Any]:
        if isinstance(message, list):
            for item in message:
                if isinstance(item, dict) and item.get("id") == request_id:
                    return StreamableHTTPMCPTransport._extract_result_from_json(item, request_id)
            raise RuntimeError("Resposta MCP HTTP em lote nao contem o id esperado.")

        if not isinstance(message, dict):
            raise RuntimeError("Resposta MCP HTTP invalida.")

        if "error" in message:
            raise RuntimeError(f"Erro MCP HTTP: {message['error']}")

        if request_id is not None and message.get("id") != request_id:
            raise RuntimeError("Resposta MCP HTTP com id inesperado.")

        return _ensure_dict(message.get("result"))

    @staticmethod
    def _extract_result_from_sse(
        payload: str,
        request_id: int | None,
    ) -> dict[str, Any]:
        for event_payload in _iter_sse_data(payload):
            message = json.loads(event_payload)
            if isinstance(message, dict) and message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"Erro MCP HTTP SSE: {message['error']}")
                return _ensure_dict(message.get("result"))

        raise RuntimeError("Nenhuma resposta MCP foi encontrada no stream SSE.")


HTTPMCPTransport = StreamableHTTPMCPTransport


def _iter_sse_data(payload: str) -> Iterable[str]:
    data_lines: list[str] = []
    for line in payload.splitlines():
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield "\n".join(data_lines)


def _ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}

"""Minimal NextEmby adapter. No redirects, retries, user or system mutations."""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import re
from typing import Callable
from urllib.parse import urlsplit

from core import validate_product


class NextEmbyError(Exception):
    """Sanitized error; never includes server response bodies or credentials."""


class GenerationUncertainError(NextEmbyError):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes


MAX_RESPONSE_BYTES = 1024 * 1024
_BATCH = re.compile(r"^BATCH_ID:\s*([A-Za-z0-9_.-]{1,128})$")
_CARD = re.compile(r"^[A-Za-z0-9_-]{4,256}$")


def validate_base_url(base_url: str, *, require_https: bool = False) -> str:
    try:
        parts = urlsplit(base_url)
        valid = parts.scheme in {"http", "https"} and parts.hostname and parts.port != 0
        valid = valid and not parts.username and not parts.password and not parts.query and not parts.fragment
        valid = valid and parts.path in {"", "/"}
        valid = valid and not any(character.isspace() or ord(character) < 32 for character in base_url)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise NextEmbyError("NextEmby 地址格式无效，请填写含 http(s):// 的主机与端口")
    if require_https and parts.scheme != 'https':
        raise NextEmbyError('当前部署要求 NextEmby 接口使用 HTTPS')
    return base_url.rstrip('/')


def _transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> Response:
    target = urlsplit(url)
    connection_type = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(target.hostname, target.port, timeout=timeout)
    try:
        connection.request(method, target.path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read(MAX_RESPONSE_BYTES + 1)
        if len(content) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        expected_length = response.getheader("Content-Length")
        if expected_length is not None and len(content) != int(expected_length):
            raise ValueError("incomplete response")
        return Response(response.status, {key.lower(): value for key, value in response.getheaders()}, content)
    finally:
        connection.close()


class NextEmbyClient:
    def __init__(self, base_url: str, token: str, *, transport: Callable | None = None, timeout: float = 20,
                 require_https: bool = False):
        self.base_url = validate_base_url(base_url, require_https=require_https)
        if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token):
            raise NextEmbyError("NextEmby 密钥格式无效")
        self._token = token
        self._transport = transport or _transport
        self.timeout = timeout

    def _request(self, method: str, endpoint: str, payload: dict | None = None) -> Response:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json, text/plain"}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            response = self._transport(method, self.base_url + endpoint, headers, body, self.timeout)
            if not isinstance(response, Response) or len(response.body) > MAX_RESPONSE_BYTES:
                raise ValueError("invalid response")
            return response
        except Exception:
            if method == "POST":
                raise GenerationUncertainError("生成请求中断或超时；远端结果未知，必须人工核实") from None
            raise NextEmbyError("无法读取 NextEmby，请检查连接与服务状态") from None

    def read_batches(self) -> list[dict]:
        response = self._request("GET", "/api/admin/cards/batches")
        if response.status != 200:
            raise NextEmbyError("NextEmby 批次接口未授权或不可用（HTTP %d）" % response.status)
        try:
            data = json.loads(response.body.decode("utf-8-sig"))
            if isinstance(data, dict):
                if data.get("success") is False or data.get("status") in {"error", "failed"}:
                    raise ValueError("failed status")
                data = data.get("batches", data.get("data"))
                if isinstance(data, dict):
                    data = data.get("batches")
            if not isinstance(data, list) or any(not isinstance(batch, dict) for batch in data):
                raise ValueError("invalid batches")
            # Never expose card strings, account data, or arbitrary server fields.
            allowed = {"batch_id", "id", "duration_days", "days", "template_name", "created_at", "created_time",
                       "count", "total", "total_count", "used", "used_count", "remaining", "remaining_count", "unused", "unused_count"}
            return [{key: value for key, value in batch.items() if key in allowed and isinstance(value, (str, int, float, bool, type(None)))} for batch in data]
        except (UnicodeError, ValueError, TypeError):
            raise NextEmbyError("NextEmby 批次返回格式与预期不符") from None

    def generate(self, product: dict) -> dict:
        product = validate_product(product)
        payload = {"duration_days": product["duration_days"], "count": 1, "template_name": product["template_name"]}
        response = self._request("POST", "/api/admin/cards/generate", payload)
        if response.status != 200:
            raise GenerationUncertainError("生成请求未获成功确认（HTTP %d），请人工核实后处理" % response.status)
        try:
            lines = [line.strip() for line in response.body.decode("utf-8-sig").splitlines() if line.strip()]
            if len(lines) != 2:
                raise ValueError("expected batch and one card")
            batch = _BATCH.fullmatch(lines[0])
            if not batch or not _CARD.fullmatch(lines[1]):
                raise ValueError("unexpected stream")
            return {"batch_id": batch.group(1), "card": lines[1]}
        except (UnicodeError, ValueError):
            raise GenerationUncertainError("生成响应未包含一个有效批次和恰好一张卡，必须人工核实") from None

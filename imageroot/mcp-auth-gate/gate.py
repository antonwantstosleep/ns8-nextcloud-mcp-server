#!/usr/bin/env python3

#
# Copyright (C) 2026 Anton
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Reject unauthenticated clients before they can list MCP tools.

Upstream cbcoutinho/nextcloud-mcp-server 0.198.3 in multi_user_basic does not.
BasicAuthMiddleware only decodes Authorization when it is present, then always
calls the app. app.py logs that a /mcp request without Authorization is
expected in BasicAuth mode. context.py raises only when a Nextcloud client is
created, and initialize / tools/list / resources/list never create one. A
client with only the URL therefore completes the handshake and Cursor shows a
green server with the full tool list.

This process is the only listener on the published pod port. The MCP server
listens on 127.0.0.1:8001 and is not published. Protected requests are proxied
only after Nextcloud accepts the same Authorization header
(GET {NEXTCLOUD_HOST}/ocs/v2.php/cloud/user). OCS v1 success is meta
statuscode 100; /ocs/v2.php success is meta statuscode 200. The user name
and app password are not written anywhere. A process-local HMAC cache stores digests, not
credentials, so a handshake does not repeat the OCS call.

/health, /health/live, and /health/ready stay open. Everything else, including
the management API, requires Basic auth. There is no shared module password.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import http.client
import json
import logging
import os
import ssl
import time
import urllib.parse

logger = logging.getLogger("mcp-auth-gate")

OPEN_PATHS = frozenset({"/health", "/health/live", "/health/ready"})
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "upgrade",
    }
)
HEADER_LIMIT = 65536
MAX_AUTHORIZATION = 4096
OCS_TIMEOUT = 10.0
UPSTREAM_CONNECT_ATTEMPTS = 10
UPSTREAM_CONNECT_DELAY = 0.05


class AuthCache:
    """Short-lived allow/deny memory. Keys are HMACs, never the password."""

    def __init__(self, ttl: float, negative_ttl: float):
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        self._mac = os.urandom(32)
        self.entries: dict[str, tuple[float, bool]] = {}

    def digest(self, authorization: str) -> str:
        return hmac.new(
            self._mac, authorization.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def get(self, authorization: str) -> bool | None:
        key = self.digest(authorization)
        found = self.entries.get(key)
        if found is None:
            return None
        expires, allowed = found
        if expires <= time.monotonic():
            self.entries.pop(key, None)
            return None
        return allowed

    def store(self, authorization: str, allowed: bool) -> None:
        ttl = self.ttl if allowed else self.negative_ttl
        if ttl <= 0:
            return
        if len(self.entries) > 1024:
            now = time.monotonic()
            self.entries = {
                key: item for key, item in self.entries.items() if item[0] > now
            }
        self.entries[self.digest(authorization)] = (time.monotonic() + ttl, allowed)


class ByteReader:
    def __init__(self, reader: asyncio.StreamReader):
        self._reader = reader
        self._buf = bytearray()

    async def _pull(self) -> bool:
        chunk = await self._reader.read(65536)
        if not chunk:
            return False
        self._buf.extend(chunk)
        return True

    async def readexactly(self, count: int) -> bytes:
        while len(self._buf) < count:
            if not await self._pull():
                raise ConnectionError("unexpected eof")
        out = bytes(self._buf[:count])
        del self._buf[:count]
        return out

    async def read_some(self, count: int) -> bytes:
        if not self._buf and not await self._pull():
            return b""
        size = min(count, len(self._buf))
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    async def readline(self, limit: int = HEADER_LIMIT) -> bytes:
        while b"\n" not in self._buf:
            if len(self._buf) > limit:
                raise ConnectionError("line too long")
            if not await self._pull():
                out = bytes(self._buf)
                self._buf.clear()
                return out
        index = self._buf.index(b"\n")
        if index > limit:
            raise ConnectionError("line too long")
        out = bytes(self._buf[: index + 1])
        del self._buf[: index + 1]
        return out


def normalize_request_path(target: str) -> str:
    """Path used for the health allowlist. Encoded .. cannot slip through."""
    raw = target
    if raw.startswith("http://") or raw.startswith("https://"):
        raw = urllib.parse.urlsplit(raw).path or "/"
    path = raw.split("?", 1)[0].split("#", 1)[0]
    path = urllib.parse.unquote(path)
    if "\\" in path or "\x00" in path:
        return "/"
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


def is_open_path(target: str) -> bool:
    return normalize_request_path(target) in OPEN_PATHS


def parse_basic(authorization: str | None) -> tuple[str, str] | None:
    """Return (user, password) when the header is usable Basic auth."""
    if authorization is None:
        return None
    if len(authorization) > MAX_AUTHORIZATION:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator == "" or scheme.lower() != "basic":
        return None
    token = "".join(token.split())
    if token == "":
        return None
    padded = token + "=" * (-len(token) % 4)
    try:
        decoded = base64.b64decode(padded, validate=True)
    except (ValueError, TypeError):
        return None
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    username, separator, password = text.partition(":")
    if separator == "" or username == "" or password == "":
        return None
    return username, password


def safe_user(username: str) -> str:
    cleaned = []
    for char in username[:80]:
        if char.isprintable() and char not in "\r\n":
            cleaned.append(char)
        else:
            cleaned.append("?")
    return "".join(cleaned)


def ocs_user_path(nextcloud_host: str) -> tuple[str, str, int, str] | None:
    """Return scheme, hostname, port, path for the OCS user probe."""
    parsed = urllib.parse.urlsplit(nextcloud_host.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    prefix = parsed.path.rstrip("/")
    return parsed.scheme, parsed.hostname, port, prefix + "/ocs/v2.php/cloud/user"


def ocs_meta_success(code: object) -> bool:
    """True when an OCS user probe meta statuscode means authenticated.

    OCS v1 reports success as 100. Nextcloud ``/ocs/v2.php`` (including
    Nextcloud 33) reports success as 200. Either value may be a JSON number
    or a string. Other types, including bool, are not success.
    """
    if type(code) is int:
        return code in (100, 200)
    if type(code) is str:
        return code in ("100", "200")
    return False


def check_nextcloud(nextcloud_host: str, authorization: str, timeout: float = OCS_TIMEOUT) -> str:
    """Return ok, rejected, or unavailable. Does not follow redirects.

    HTTP 200 plus meta statuscode 100 or 200 is success. HTTP 200 with any
    other statuscode, and client errors such as 401, are denials. Redirects,
    404, and 5xx are unavailable so a transient Nextcloud failure is not
    cached as a rejected password.
    """
    target = ocs_user_path(nextcloud_host)
    if target is None:
        logger.warning("NEXTCLOUD_HOST is missing or not an http(s) URL")
        return "unavailable"
    scheme, hostname, port, path = target
    connection = None
    try:
        if scheme == "https":
            connection = http.client.HTTPSConnection(
                hostname,
                port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(hostname, port, timeout=timeout)
        connection.request(
            "GET",
            path,
            headers={
                "Authorization": authorization,
                "OCS-APIRequest": "true",
                "Accept": "application/json",
                "User-Agent": "ns8-mcp-auth-gate",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        body = response.read(65536)
        status = response.status
    except Exception as exc:
        logger.warning("Nextcloud credential check failed: %s", type(exc).__name__)
        return "unavailable"
    finally:
        if connection is not None:
            connection.close()

    if status in (301, 302, 303, 307, 308) or status >= 500 or status == 404:
        logger.warning("Nextcloud credential check HTTP %s", status)
        return "unavailable"
    if status != 200:
        return "rejected"
    try:
        payload = json.loads(body.decode("utf-8"))
        code = payload["ocs"]["meta"]["statuscode"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return "rejected"
    if ocs_meta_success(code):
        return "ok"
    return "rejected"


def error_bytes(status: int, reason: str, error: str, message: str) -> bytes:
    body = json.dumps({"error": error, "message": message}).encode("utf-8")
    lines = [
        f"HTTP/1.1 {status} {reason}",
        "Content-Type: application/json; charset=utf-8",
        f"Content-Length: {len(body)}",
        "Cache-Control: no-store",
        "Connection: close",
        "Server: ns8-mcp-auth-gate",
        "X-Content-Type-Options: nosniff",
    ]
    if status == 401:
        lines.insert(1, 'WWW-Authenticate: Basic realm="Nextcloud"')
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


async def write_all(writer: asyncio.StreamWriter, payload: bytes) -> None:
    writer.write(payload)
    await writer.drain()


def header_lookup(headers: list[tuple[str, str]], name: str) -> list[str]:
    folded = name.lower()
    return [value for key, value in headers if key.lower() == folded]


def request_framing(headers: list[tuple[str, str]]) -> tuple[str, int | None]:
    """Return ('chunked', None), ('length', n), or ('none', None)."""
    encodings = header_lookup(headers, "transfer-encoding")
    lengths = header_lookup(headers, "content-length")
    if encodings and lengths:
        raise ValueError("both content-length and transfer-encoding")
    if encodings:
        if len(encodings) != 1 or encodings[0].strip().lower() != "chunked":
            raise ValueError("unsupported transfer-encoding")
        return "chunked", None
    if lengths:
        if len(lengths) != 1:
            raise ValueError("repeated content-length")
        try:
            length = int(lengths[0].strip())
        except ValueError as exc:
            raise ValueError("bad content-length") from exc
        if length < 0:
            raise ValueError("bad content-length")
        return "length", length
    return "none", None


def forward_headers(headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    blocked = set(HOP_BY_HOP)
    for value in header_lookup(headers, "connection"):
        for name in value.split(","):
            blocked.add(name.strip().lower())
    blocked.add("expect")
    kept = [(name, value) for name, value in headers if name.lower() not in blocked]
    kept.append(("Connection", "close"))
    return kept


async def read_headers(reader: ByteReader) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    total = 0
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n"):
            return headers
        if line == b"":
            raise ConnectionError("unexpected eof")
        total += len(line)
        if total > HEADER_LIMIT or len(headers) > 100:
            raise ConnectionError("headers too large")
        if b":" not in line:
            raise ConnectionError("bad header")
        raw = line.decode("latin-1").rstrip("\r\n")
        name, value = raw.split(":", 1)
        if name == "" or " " in name or "\t" in name:
            raise ConnectionError("bad header")
        headers.append((name, value.strip()))


async def pipe_exact(src: ByteReader, dst: asyncio.StreamWriter, count: int) -> None:
    remaining = count
    while remaining:
        chunk = await src.readexactly(min(65536, remaining))
        remaining -= len(chunk)
        await write_all(dst, chunk)


async def pipe_chunked(src: ByteReader, dst: asyncio.StreamWriter) -> None:
    while True:
        line = await src.readline()
        if line == b"":
            raise ConnectionError("truncated chunk")
        await write_all(dst, line)
        size_token = line.split(b";", 1)[0].strip()
        try:
            size = int(size_token, 16)
        except ValueError as exc:
            raise ConnectionError("bad chunk size") from exc
        if size < 0:
            raise ConnectionError("bad chunk size")
        if size == 0:
            while True:
                trailer = await src.readline()
                if trailer == b"":
                    raise ConnectionError("truncated chunk trailer")
                await write_all(dst, trailer)
                if trailer in (b"\r\n", b"\n"):
                    return
        remaining = size
        while remaining:
            chunk = await src.readexactly(min(65536, remaining))
            remaining -= len(chunk)
            await write_all(dst, chunk)
        marker = await src.readexactly(2)
        if marker != b"\r\n":
            raise ConnectionError("bad chunk terminator")
        await write_all(dst, marker)


async def open_upstream(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    last_error: Exception | None = None
    for attempt in range(UPSTREAM_CONNECT_ATTEMPTS):
        try:
            return await asyncio.wait_for(asyncio.open_connection(host, port), 2)
        except (OSError, asyncio.TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < UPSTREAM_CONNECT_ATTEMPTS:
                await asyncio.sleep(UPSTREAM_CONNECT_DELAY)
    assert last_error is not None
    raise last_error


async def authorize(
    authorization: str | None,
    nextcloud_host: str,
    cache: AuthCache,
) -> tuple[int, str, str] | None:
    """None means proxy. Otherwise an HTTP error tuple."""
    parsed = parse_basic(authorization)
    if authorization is None or authorization == "":
        logger.info("rejected MCP request: missing Authorization")
        return (
            401,
            "unauthorized",
            "HTTP Basic authentication with a Nextcloud user and app password is required",
        )
    if parsed is None:
        logger.info("rejected MCP request: malformed Authorization")
        return (
            401,
            "unauthorized",
            "Authorization must be Basic base64(nextcloud_user:app_password)",
        )
    username, _password = parsed
    cached = cache.get(authorization)
    if cached is True:
        return None
    if cached is False:
        logger.info("rejected MCP request: cached denial for %s", safe_user(username))
        return (401, "unauthorized", "Nextcloud rejected the credentials")
    if not nextcloud_host.strip():
        logger.error("rejected MCP request: NEXTCLOUD_HOST is empty")
        return (502, "bad_gateway", "NEXTCLOUD_HOST is not configured")

    verdict = await asyncio.to_thread(check_nextcloud, nextcloud_host, authorization)
    if verdict == "ok":
        cache.store(authorization, True)
        logger.info("accepted MCP credentials for %s", safe_user(username))
        return None
    if verdict == "rejected":
        cache.store(authorization, False)
        logger.info("rejected MCP request: Nextcloud denied %s", safe_user(username))
        return (401, "unauthorized", "Nextcloud rejected the credentials")
    logger.warning("could not validate MCP credentials for %s", safe_user(username))
    return (502, "bad_gateway", "Could not validate credentials with Nextcloud")


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    upstream_host: str,
    upstream_port: int,
    nextcloud_host: str,
    cache: AuthCache,
) -> None:
    upstream: asyncio.StreamWriter | None = None
    responded = False

    async def send_error(status: int, reason: str, error: str, message: str) -> None:
        nonlocal responded
        if responded:
            return
        responded = True
        await write_all(writer, error_bytes(status, reason, error, message))

    try:
        incoming = ByteReader(reader)
        try:
            request_line = await asyncio.wait_for(incoming.readline(), 30)
        except asyncio.TimeoutError:
            return
        if request_line == b"":
            return
        parts = request_line.decode("latin-1").rstrip("\r\n").split()
        if len(parts) != 3:
            await send_error(400, "Bad Request", "bad_request", "Bad request")
            return
        method, target, version = parts
        if version not in ("HTTP/1.0", "HTTP/1.1"):
            await send_error(400, "Bad Request", "bad_request", "Bad request")
            return
        headers = await read_headers(incoming)
        try:
            framing, length = request_framing(headers)
        except ValueError:
            await send_error(400, "Bad Request", "bad_request", "Bad request")
            return
        if method.upper() == "HEAD":
            framing, length = "none", None

        if not is_open_path(target):
            authorization = header_lookup(headers, "authorization")
            decision = await authorize(
                authorization[-1] if authorization else None,
                nextcloud_host,
                cache,
            )
            if decision is not None:
                status, error, message = decision
                reason = "Unauthorized" if status == 401 else "Bad Gateway"
                await send_error(status, reason, error, message)
                return

        try:
            upstream_reader, upstream = await open_upstream(upstream_host, upstream_port)
        except (OSError, asyncio.TimeoutError):
            logger.warning("MCP upstream %s:%s is not reachable", upstream_host, upstream_port)
            await send_error(502, "Bad Gateway", "bad_gateway", "MCP server is not reachable")
            return

        forwarded = forward_headers(headers)
        head = [f"{method} {target} {version}"]
        head.extend(f"{name}: {value}" for name, value in forwarded)
        await write_all(upstream, ("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
        if framing == "chunked":
            await pipe_chunked(incoming, upstream)
        elif framing == "length" and length:
            await pipe_exact(incoming, upstream, length)
        while True:
            chunk = await upstream_reader.read(65536)
            if not chunk:
                if not responded:
                    await send_error(
                        502,
                        "Bad Gateway",
                        "bad_gateway",
                        "MCP server closed the connection",
                    )
                return
            responded = True
            await write_all(writer, chunk)
    except (ConnectionError, asyncio.TimeoutError, UnicodeError, OSError) as exc:
        logger.warning("MCP proxy closed: %s", type(exc).__name__)
        if not writer.is_closing():
            try:
                await send_error(400, "Bad Request", "bad_request", "Bad request")
            except (ConnectionError, OSError):
                pass
    finally:
        if upstream is not None:
            upstream.close()
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def serve(
    listen_host: str,
    listen_port: int,
    upstream_host: str,
    upstream_port: int,
    nextcloud_host: str,
    *,
    cache_ttl: float = 60,
    negative_ttl: float = 5,
    cache: AuthCache | None = None,
    ready: asyncio.Event | None = None,
    bound: dict | None = None,
) -> None:
    auth_cache = cache if cache is not None else AuthCache(cache_ttl, negative_ttl)

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await handle_client(
            reader,
            writer,
            upstream_host,
            upstream_port,
            nextcloud_host,
            auth_cache,
        )

    server = await asyncio.start_server(client, listen_host, listen_port)
    sockets = server.sockets or []
    if bound is not None and sockets:
        bound["port"] = sockets[0].getsockname()[1]
    logger.info(
        "listening on %s:%s, upstream http://%s:%s, health paths stay open, credentials are not stored",
        listen_host,
        sockets[0].getsockname()[1] if sockets else listen_port,
        upstream_host,
        upstream_port,
    )
    if ready is not None:
        ready.set()
    async with server:
        await server.serve_forever()


def _split_listen(value: str) -> tuple[str, int]:
    host, separator, port = value.rpartition(":")
    if separator == "" or host == "":
        raise SystemExit("MCP_AUTH_GATE_LISTEN must be host:port")
    return host, int(port)


def _split_upstream(value: str) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname:
        raise SystemExit("MCP_AUTH_GATE_UPSTREAM must be http://host:port")
    return parsed.hostname, parsed.port or 80


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    listen_host, listen_port = _split_listen(
        os.environ.get("MCP_AUTH_GATE_LISTEN", "0.0.0.0:8000")
    )
    upstream_host, upstream_port = _split_upstream(
        os.environ.get("MCP_AUTH_GATE_UPSTREAM", "http://127.0.0.1:8001")
    )
    nextcloud_host = os.environ.get("NEXTCLOUD_HOST", "")
    cache_ttl = float(os.environ.get("MCP_AUTH_GATE_CACHE_TTL", "60"))
    negative_ttl = float(os.environ.get("MCP_AUTH_GATE_NEGATIVE_TTL", "5"))
    asyncio.run(
        serve(
            listen_host,
            listen_port,
            upstream_host,
            upstream_port,
            nextcloud_host,
            cache_ttl=cache_ttl,
            negative_ttl=negative_ttl,
        )
    )


if __name__ == "__main__":
    main()

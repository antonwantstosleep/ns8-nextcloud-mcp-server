#
# Copyright (C) 2026 Anton
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""The published /mcp port rejects clients that have no Nextcloud Basic auth."""

import asyncio
import base64
import contextlib
import json
import logging
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "imageroot" / "mcp-auth-gate"))

import gate  # noqa: E402

logging.disable(logging.CRITICAL)

ALICE = "alice"
GOOD = "good-app-password"
INIT = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "0"},
        },
    }
).encode()
TOOLS = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode()
INIT_RESULT = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "upstream", "version": "0"},
        },
    }
).encode()


def basic(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return "Basic " + token


def header_value(headers, name):
    folded = name.lower()
    for key, value in headers:
        if key.lower() == folded:
            return value
    return None


async def read_body(incoming, headers):
    framing, length = gate.request_framing(headers)
    if framing == "length":
        if not length:
            return b""
        return await incoming.readexactly(length)
    if framing == "chunked":
        parts = []
        while True:
            line = await incoming.readline()
            size = int(line.split(b";", 1)[0].strip(), 16)
            if size == 0:
                while True:
                    trailer = await incoming.readline()
                    if trailer in (b"\r\n", b"\n"):
                        return b"".join(parts)
            parts.append(await incoming.readexactly(size))
            marker = await incoming.readexactly(2)
            if marker != b"\r\n":
                raise AssertionError(marker)
    return b""


async def read_request(reader):
    incoming = gate.ByteReader(reader)
    line = await incoming.readline()
    headers = await gate.read_headers(incoming)
    body = await read_body(incoming, headers)
    return line.decode("latin-1").rstrip("\r\n"), headers, body


def http_ok(body, extra=b""):
    return (
        b"HTTP/1.1 200 OK\r\n"
        + extra
        + b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )


def parse_response(data):
    head, body = data.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        name, value = line.split(":", 1)
        headers[name.lower()] = value.strip()
    if "content-length" in headers:
        body = body[: int(headers["content-length"])]
    return status, headers, body


def ocs_user_body(statuscode, message="OK"):
    success = statuscode in (100, "100", 200, "200")
    return json.dumps(
        {
            "ocs": {
                "meta": {
                    "status": "ok" if success else "failure",
                    "statuscode": statuscode,
                    "message": message,
                },
                "data": {"id": ALICE} if success else [],
            }
        }
    ).encode()


def _http_response(status_line, body):
    raw = body if isinstance(body, bytes) else body.encode()
    head = (
        f"HTTP/1.1 {status_line}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(raw)}\r\n"
        "Connection: close\r\n\r\n"
    )
    return head.encode("ascii") + raw


async def ocs_verdict(status_line, body):
    async def handler(reader, writer):
        try:
            await read_request(reader)
            writer.write(_http_response(status_line, body))
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        return await asyncio.to_thread(
            gate.check_nextcloud,
            f"http://127.0.0.1:{port}",
            basic(ALICE, GOOD),
        )
    finally:
        server.close()
        await server.wait_closed()


async def proxy_ocs_probe(status_line, body, cache_ttl=60, negative_ttl=30):
    """POST /mcp through a gate whose Nextcloud probe returns status_line/body."""
    seen = {"mcp": 0}
    auth = basic(ALICE, GOOD)

    async def ocs_client(reader, writer):
        try:
            line, headers, _body = await read_request(reader)
            seen["request"] = line
            seen["authorization"] = header_value(headers, "authorization")
            writer.write(_http_response(status_line, body))
            await writer.drain()
        finally:
            writer.close()

    async def mcp_client(reader, writer):
        try:
            await read_request(reader)
            seen["mcp"] += 1
            writer.write(http_ok(INIT_RESULT, extra=b"Mcp-Session-Id: session-1\r\n"))
            await writer.drain()
        finally:
            writer.close()

    ocs = await asyncio.start_server(ocs_client, "127.0.0.1", 0)
    mcp = await asyncio.start_server(mcp_client, "127.0.0.1", 0)
    ready = asyncio.Event()
    bound = {}
    cache = gate.AuthCache(cache_ttl, negative_ttl)
    task = asyncio.create_task(
        gate.serve(
            "127.0.0.1",
            0,
            "127.0.0.1",
            mcp.sockets[0].getsockname()[1],
            f"http://127.0.0.1:{ocs.sockets[0].getsockname()[1]}",
            cache=cache,
            ready=ready,
            bound=bound,
        )
    )
    try:
        await asyncio.wait_for(ready.wait(), 2)
        raw = await exchange(
            bound["port"],
            request_bytes("POST", "/mcp", INIT, authorization=auth),
        )
        status, _headers, response = parse_response(raw)
        return status, response, seen, cache, auth
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        ocs.close()
        mcp.close()
        await ocs.wait_closed()
        await mcp.wait_closed()


def request_bytes(method, target, body=b"", authorization=None, chunked=False):
    lines = [f"{method} {target} HTTP/1.1", "Host: mcp.example.com", "Accept: application/json, text/event-stream"]
    if authorization is not None:
        lines.append("Authorization: " + authorization)
    lines.append("Content-Type: application/json")
    if chunked:
        lines.append("Transfer-Encoding: chunked")
        payload = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
    else:
        lines.append(f"Content-Length: {len(body)}")
        payload = body
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + payload


async def exchange(port, payload, timeout=3):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(payload)
        await writer.drain()
        chunks = []
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await writer.wait_closed()


class GateBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ocs_hits = []
        self.mcp_hits = []
        self.ocs = await asyncio.start_server(self.ocs_client, "127.0.0.1", 0)
        self.mcp = await asyncio.start_server(self.mcp_client, "127.0.0.1", 0)
        self.cache = gate.AuthCache(60, 30)
        self.ready = asyncio.Event()
        self.bound = {}
        ocs_port = self.ocs.sockets[0].getsockname()[1]
        mcp_port = self.mcp.sockets[0].getsockname()[1]
        self.gate_task = asyncio.create_task(
            gate.serve(
                "127.0.0.1",
                0,
                "127.0.0.1",
                mcp_port,
                f"http://127.0.0.1:{ocs_port}",
                cache=self.cache,
                ready=self.ready,
                bound=self.bound,
            )
        )
        await asyncio.wait_for(self.ready.wait(), 2)
        self.port = self.bound["port"]

    async def asyncTearDown(self):
        self.gate_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.gate_task
        self.ocs.close()
        self.mcp.close()
        await self.ocs.wait_closed()
        await self.mcp.wait_closed()

    async def ocs_client(self, reader, writer):
        try:
            _line, headers, _body = await read_request(reader)
            auth = header_value(headers, "authorization")
            self.ocs_hits.append(auth)
            parsed = gate.parse_basic(auth)
            if parsed == (ALICE, GOOD):
                payload = {
                    "ocs": {
                        "meta": {"status": "ok", "statuscode": 100, "message": "OK"},
                        "data": {"id": ALICE},
                    }
                }
                status = "200 OK"
            else:
                payload = {
                    "ocs": {
                        "meta": {
                            "status": "failure",
                            "statuscode": 997,
                            "message": "Current user is not logged in",
                        },
                        "data": [],
                    }
                }
                status = "401 Unauthorized"
            raw = json.dumps(payload).encode()
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nConnection: close\r\n\r\n".encode()
                + raw
            )
            await writer.drain()
        finally:
            writer.close()

    async def mcp_client(self, reader, writer):
        try:
            line, headers, body = await read_request(reader)
            self.mcp_hits.append((line, headers, body))
            writer.write(http_ok(INIT_RESULT, extra=b"Mcp-Session-Id: session-1\r\n"))
            await writer.drain()
        finally:
            writer.close()

    def assert_password_not_stored(self):
        blob = json.dumps(self.cache.entries)
        self.assertNotIn(GOOD, blob)
        self.assertNotIn("super-secret-app", blob)
        self.assertNotIn(ALICE, blob)
        for key in self.cache.entries:
            self.assertEqual(len(key), 64)

    async def test_initialize_without_authorization_is_401_and_does_not_reach_mcp(self):
        raw = await exchange(self.port, request_bytes("POST", "/mcp", INIT))
        status, headers, body = parse_response(raw)
        self.assertEqual(status, 401)
        self.assertEqual(headers.get("server"), "ns8-mcp-auth-gate")
        self.assertIn("Basic", headers.get("www-authenticate", ""))
        self.assertNotIn(b"protocolVersion", body)
        self.assertNotIn(b"tools", body)
        self.assertEqual(self.mcp_hits, [])
        self.assertEqual(self.ocs_hits, [])

    async def test_tools_list_without_authorization_is_rejected(self):
        raw = await exchange(self.port, request_bytes("POST", "/mcp", TOOLS))
        status, _headers, body = parse_response(raw)
        self.assertEqual(status, 401)
        self.assertNotIn(b"jsonrpc", body)
        self.assertEqual(self.mcp_hits, [])

    async def test_get_mcp_without_authorization_is_rejected(self):
        raw = await exchange(self.port, request_bytes("GET", "/mcp"))
        status, _headers, _body = parse_response(raw)
        self.assertEqual(status, 401)
        self.assertEqual(self.mcp_hits, [])

    async def test_malformed_and_non_basic_headers_do_not_call_nextcloud(self):
        cases = [
            "Bearer anything",
            basic("alice", ""),
            "Basic !!!",
            basic("", "secret"),
        ]
        # basic() with empty password still encodes "alice:" which parse_basic rejects.
        for authorization in cases:
            raw = await exchange(
                self.port, request_bytes("POST", "/mcp", INIT, authorization=authorization)
            )
            status, _headers, body = parse_response(raw)
            self.assertEqual(status, 401, authorization)
            self.assertNotIn(b"super-secret-app", body)
        self.assertEqual(self.ocs_hits, [])
        self.assertEqual(self.mcp_hits, [])

    async def test_rejected_nextcloud_password_does_not_reach_mcp(self):
        secret = "super-secret-app"
        raw = await exchange(
            self.port,
            request_bytes("POST", "/mcp", INIT, authorization=basic(ALICE, secret)),
        )
        status, _headers, body = parse_response(raw)
        self.assertEqual(status, 401)
        self.assertNotIn(secret.encode(), body)
        self.assertEqual(len(self.ocs_hits), 1)
        self.assertEqual(self.mcp_hits, [])
        self.assert_password_not_stored()

    async def test_valid_basic_auth_proxies_initialize(self):
        raw = await exchange(
            self.port,
            request_bytes(
                "POST",
                "/mcp",
                INIT,
                authorization=basic(ALICE, GOOD),
            ),
        )
        status, headers, body = parse_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("mcp-session-id"), "session-1")
        self.assertEqual(body, INIT_RESULT)
        self.assertEqual(len(self.mcp_hits), 1)
        line, mcp_headers, mcp_body = self.mcp_hits[0]
        self.assertTrue(line.startswith("POST /mcp "))
        self.assertEqual(header_value(mcp_headers, "authorization"), basic(ALICE, GOOD))
        self.assertEqual(mcp_body, INIT)
        self.assertEqual(len(self.ocs_hits), 1)
        self.assertEqual(self.ocs_hits[0], basic(ALICE, GOOD))
        self.assert_password_not_stored()

    async def test_second_valid_request_reuses_the_digest_cache(self):
        auth = basic(ALICE, GOOD)
        for _ in range(2):
            raw = await exchange(self.port, request_bytes("POST", "/mcp", INIT, authorization=auth))
            status, _headers, body = parse_response(raw)
            self.assertEqual(status, 200)
            self.assertEqual(body, INIT_RESULT)
        self.assertEqual(len(self.ocs_hits), 1)
        self.assertEqual(len(self.mcp_hits), 2)
        self.assert_password_not_stored()

    async def test_chunked_authenticated_body_is_forwarded(self):
        raw = await exchange(
            self.port,
            request_bytes(
                "POST",
                "/mcp",
                INIT,
                authorization=basic(ALICE, GOOD),
                chunked=True,
            ),
        )
        status, _headers, body = parse_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body, INIT_RESULT)
        self.assertEqual(self.mcp_hits[0][2], INIT)

    async def test_health_stays_open_and_does_not_call_nextcloud(self):
        raw = await exchange(self.port, request_bytes("GET", "/health/live"))
        status, _headers, body = parse_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body, INIT_RESULT)
        self.assertEqual(self.ocs_hits, [])
        self.assertEqual(len(self.mcp_hits), 1)
        self.assertTrue(self.mcp_hits[0][0].startswith("GET /health/live "))

    async def test_encoded_health_traversal_is_not_anonymous(self):
        for target in ("/health/../mcp", "/health/live%2f%2e%2e/mcp", "/mcp?x=1"):
            before = len(self.mcp_hits)
            raw = await exchange(self.port, request_bytes("POST", target, TOOLS))
            status, _headers, _body = parse_response(raw)
            self.assertEqual(status, 401, target)
            self.assertEqual(len(self.mcp_hits), before)

    async def test_upstream_down_after_valid_auth_is_502(self):
        closed = socket_port()
        ready = asyncio.Event()
        bound = {}
        ocs_port = self.ocs.sockets[0].getsockname()[1]
        task = asyncio.create_task(
            gate.serve(
                "127.0.0.1",
                0,
                "127.0.0.1",
                closed,
                f"http://127.0.0.1:{ocs_port}",
                cache=gate.AuthCache(0, 0),
                ready=ready,
                bound=bound,
            )
        )
        try:
            await asyncio.wait_for(ready.wait(), 2)
            raw = await exchange(
                bound["port"],
                request_bytes("POST", "/mcp", INIT, authorization=basic(ALICE, GOOD)),
                timeout=5,
            )
            status, headers, _body = parse_response(raw)
            self.assertEqual(status, 502)
            self.assertEqual(headers.get("server"), "ns8-mcp-auth-gate")
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_missing_auth_does_not_wait_on_dead_upstream(self):
        closed = socket_port()
        ready = asyncio.Event()
        bound = {}
        task = asyncio.create_task(
            gate.serve(
                "127.0.0.1",
                0,
                "127.0.0.1",
                closed,
                "http://127.0.0.1:9",
                cache=gate.AuthCache(0, 0),
                ready=ready,
                bound=bound,
            )
        )
        try:
            await asyncio.wait_for(ready.wait(), 2)
            raw = await exchange(bound["port"], request_bytes("POST", "/mcp", TOOLS))
            status, _headers, _body = parse_response(raw)
            self.assertEqual(status, 401)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_streamed_upstream_response_is_not_buffered(self):
        release = asyncio.Event()
        ocs = await asyncio.start_server(self.ocs_client, "127.0.0.1", 0)

        async def streaming_mcp(reader, writer):
            try:
                await read_request(reader)
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\n"
                    b"Connection: close\r\n\r\n"
                )
                first = b"data: first-event\n\n"
                writer.write(f"{len(first):x}\r\n".encode() + first + b"\r\n")
                await writer.drain()
                await release.wait()
                second = b"data: second-event\n\n"
                writer.write(f"{len(second):x}\r\n".encode() + second + b"\r\n")
                writer.write(b"0\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()

        mcp = await asyncio.start_server(streaming_mcp, "127.0.0.1", 0)
        ready = asyncio.Event()
        bound = {}
        task = asyncio.create_task(
            gate.serve(
                "127.0.0.1",
                0,
                "127.0.0.1",
                mcp.sockets[0].getsockname()[1],
                f"http://127.0.0.1:{ocs.sockets[0].getsockname()[1]}",
                cache=gate.AuthCache(0, 0),
                ready=ready,
                bound=bound,
            )
        )
        reader = writer = None
        try:
            await asyncio.wait_for(ready.wait(), 2)
            reader, writer = await asyncio.open_connection("127.0.0.1", bound["port"])
            writer.write(
                request_bytes("POST", "/mcp", INIT, authorization=basic(ALICE, GOOD))
            )
            await writer.drain()
            collected = b""
            while b"first-event" not in collected:
                collected += await asyncio.wait_for(reader.read(4096), 2)
            release.set()
            while b"second-event" not in collected:
                collected += await asyncio.wait_for(reader.read(4096), 2)
        finally:
            release.set()
            if writer is not None:
                writer.close()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            mcp.close()
            ocs.close()
            await mcp.wait_closed()
            await ocs.wait_closed()

    async def test_nextcloud_redirect_is_unavailable(self):
        async def redirect(reader, writer):
            try:
                await read_request(reader)
                writer.write(
                    b"HTTP/1.1 302 Found\r\nLocation: https://cloud.example/ocs\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
            finally:
                writer.close()

        server = await asyncio.start_server(redirect, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            verdict = await asyncio.to_thread(
                gate.check_nextcloud,
                f"http://127.0.0.1:{port}",
                basic(ALICE, GOOD),
            )
            self.assertEqual(verdict, "unavailable")
        finally:
            server.close()
            await server.wait_closed()

    async def test_ocs_v2_statuscode_200_is_accepted_and_proxied(self):
        """Nextcloud 33 /ocs/v2.php success is HTTP 200 and meta statuscode 200.

        Accepting only OCS v1 statuscode 100 classifies that probe as a denial
        and answers 401 even though Nextcloud already accepted the app password.
        """
        for statuscode in (200, "200"):
            with self.subTest(statuscode=repr(statuscode)):
                status, body, seen, cache, auth = await proxy_ocs_probe(
                    "200 OK", ocs_user_body(statuscode)
                )
                self.assertEqual(status, 200)
                self.assertEqual(body, INIT_RESULT)
                self.assertEqual(seen["mcp"], 1)
                self.assertTrue(seen["request"].startswith("GET /ocs/v2.php/cloud/user "))
                self.assertEqual(seen["authorization"], auth)
                self.assertIs(cache.get(auth), True)
                self.assertNotIn(GOOD, json.dumps(cache.entries))
                self.assertNotIn(GOOD.encode(), body)

    async def test_ocs_v1_statuscode_100_is_still_accepted_and_proxied(self):
        for statuscode in (100, "100"):
            with self.subTest(statuscode=repr(statuscode)):
                status, body, seen, cache, auth = await proxy_ocs_probe(
                    "200 OK", ocs_user_body(statuscode)
                )
                self.assertEqual(status, 200)
                self.assertEqual(body, INIT_RESULT)
                self.assertEqual(seen["mcp"], 1)
                self.assertIs(cache.get(auth), True)
                self.assertNotIn(GOOD, json.dumps(cache.entries))

    async def test_http_200_other_statuscode_is_401_and_not_cached_as_success(self):
        for statuscode in (997, "997", 401, "401", 0):
            with self.subTest(statuscode=repr(statuscode)):
                status, body, seen, cache, auth = await proxy_ocs_probe(
                    "200 OK",
                    ocs_user_body(statuscode, message="Current user is not logged in"),
                )
                self.assertEqual(status, 401)
                self.assertIn(b"Nextcloud rejected the credentials", body)
                self.assertEqual(seen["mcp"], 0)
                self.assertIs(cache.get(auth), False)
                self.assertNotIn(GOOD, json.dumps(cache.entries))
                self.assertNotIn(GOOD.encode(), body)

    async def test_http_401_is_rejected_even_when_body_statuscode_is_200(self):
        status, body, seen, cache, auth = await proxy_ocs_probe(
            "401 Unauthorized", ocs_user_body(200)
        )
        self.assertEqual(status, 401)
        self.assertIn(b"Nextcloud rejected the credentials", body)
        self.assertNotIn(b"Could not validate credentials with Nextcloud", body)
        self.assertEqual(seen["mcp"], 0)
        self.assertIs(cache.get(auth), False)

    async def test_redirect_5xx_and_404_stay_unavailable(self):
        # A success-shaped body must not turn these into an accept or a denial.
        success_body = ocs_user_body(200)
        for status_line in (
            "301 Moved Permanently",
            "302 Found",
            "303 See Other",
            "307 Temporary Redirect",
            "308 Permanent Redirect",
            "404 Not Found",
            "500 Internal Server Error",
            "503 Service Unavailable",
        ):
            with self.subTest(status_line=status_line):
                self.assertEqual(await ocs_verdict(status_line, success_body), "unavailable")

        for status_line in ("404 Not Found", "500 Internal Server Error"):
            with self.subTest(gate=status_line):
                status, body, seen, cache, auth = await proxy_ocs_probe(
                    status_line, success_body
                )
                self.assertEqual(status, 502)
                self.assertIn(b"Could not validate credentials with Nextcloud", body)
                self.assertNotIn(b"Nextcloud rejected the credentials", body)
                self.assertEqual(seen["mcp"], 0)
                self.assertIsNone(cache.get(auth))
                self.assertNotIn(GOOD, json.dumps(cache.entries))


def socket_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class GateUnitTests(unittest.TestCase):
    def test_ocs_meta_success_accepts_v1_100_and_v2_200_only(self):
        for code in (100, "100", 200, "200"):
            self.assertTrue(gate.ocs_meta_success(code), repr(code))
        for code in (True, False, 200.0, 100.0, "200 ", " 100", 997, "997", None, 0, "ok"):
            self.assertFalse(gate.ocs_meta_success(code), repr(code))

    def test_basic_parser_and_health_allowlist(self):
        self.assertIsNone(gate.parse_basic(None))
        self.assertIsNone(gate.parse_basic("Bearer token"))
        self.assertIsNone(gate.parse_basic(basic("alice", "")))
        self.assertEqual(gate.parse_basic(basic("alice", "p:ass")), ("alice", "p:ass"))
        self.assertTrue(gate.is_open_path("/health/live"))
        self.assertTrue(gate.is_open_path("/health/ready?probe=1"))
        self.assertFalse(gate.is_open_path("/health/../mcp"))
        self.assertFalse(gate.is_open_path("/health/live%2f%2e%2e/mcp"))
        self.assertFalse(gate.is_open_path("/mcp"))
        self.assertEqual(
            gate.normalize_request_path("https://mcp.example.com/health/../mcp"),
            "/mcp",
        )

    def test_gate_source_does_not_name_stored_nextcloud_credentials(self):
        source = (ROOT / "imageroot" / "mcp-auth-gate" / "gate.py").read_text()
        self.assertNotIn("NEXTCLOUD_PASSWORD", source)
        self.assertNotIn("NEXTCLOUD_USERNAME", source)

    def test_systemd_publishes_the_gate_and_hides_mcp(self):
        server = (
            ROOT / "imageroot" / "systemd" / "user" / "nextcloud-mcp-server.service"
        ).read_text()
        gate_unit = (
            ROOT / "imageroot" / "systemd" / "user" / "nextcloud-mcp-auth-gate.service"
        ).read_text()
        pod = (ROOT / "imageroot" / "systemd" / "user" / "nextcloud-mcp.service").read_text()
        self.assertIn("run --host 127.0.0.1 --port 8001", server)
        self.assertIn("--entrypoint /opt/venv/bin/nextcloud-mcp-server", server)
        exec_start = gate_unit.split("ExecStart=", 1)[1].split("ExecStop=", 1)[0]
        self.assertNotIn("secrets.env", exec_start)
        self.assertNotIn("NEXTCLOUD_PASSWORD", exec_start)
        self.assertNotIn("NEXTCLOUD_USERNAME", exec_start)
        self.assertIn("MCP_AUTH_GATE_LISTEN=0.0.0.0:8000", gate_unit)
        self.assertIn("MCP_AUTH_GATE_UPSTREAM=http://127.0.0.1:8001", gate_unit)
        self.assertIn("%E/mcp-auth-gate/gate.py", gate_unit)
        self.assertNotIn("/imageroot/mcp-auth-gate/gate.py", gate_unit)
        self.assertIn("chmod 0644", gate_unit)
        self.assertIn("nextcloud-mcp-auth-gate.service", pod)
        publish = [line for line in pod.splitlines() if "--publish" in line]
        self.assertEqual(publish, ["    --publish 127.0.0.1:${TCP_PORT}:8000 \\"])

    def test_settings_ui_has_no_password_field(self):
        settings = (ROOT / "ui" / "src" / "views" / "Settings.vue").read_text()
        self.assertNotIn("nextcloud_password", settings)
        self.assertNotIn("nextcloud_username", settings)
        self.assertNotIn('type="password"', settings)
        schema = (
            ROOT / "imageroot" / "actions" / "configure-module" / "validate-input.json"
        ).read_text()
        self.assertIn("Not stored", schema)

    def test_packaged_install_root_contains_gate(self):
        """extract-image must leave gate.py where the unit copies it from.

        build-images.sh adds the whole imageroot directory. NS8 then runs
        extract-image, which unpacks that directory with the imageroot
        prefix stripped into the module install dir (%E). A unit that
        copies /imageroot/mcp-auth-gate/gate.py looks for a path that
        exists only inside the image, so cp fails and the pod is torn down.
        """
        build = (ROOT / "build-images.sh").read_text()
        self.assertIn('buildah add "${container}" imageroot /imageroot', build)

        gate_unit = (
            ROOT / "imageroot" / "systemd" / "user" / "nextcloud-mcp-auth-gate.service"
        ).read_text()
        copies = [
            line.split("ExecStartPre=", 1)[1].strip()
            for line in gate_unit.splitlines()
            if line.startswith("ExecStartPre=/bin/cp ") and "gate.py" in line
        ]
        self.assertEqual(
            copies,
            ["/bin/cp -f %E/mcp-auth-gate/gate.py %S/state/mcp-auth-gate.py"],
        )
        source_spec = copies[0].split()[2]

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "image.tar"
            install = Path(tmp) / "install"
            install.mkdir()
            subprocess.run(
                ["tar", "-cf", str(archive), "-C", str(ROOT), "imageroot"],
                check=True,
            )
            # Same filters as ns8-core usr/local/agent/bin/extract-image.
            subprocess.run(
                [
                    "tar",
                    "--no-overwrite-dir",
                    "--no-same-owner",
                    "--exclude-caches-under",
                    "--exclude=.gitignore",
                    "--strip-components=1",
                    "-x",
                    "-f",
                    str(archive),
                    "-C",
                    str(install),
                    "imageroot",
                ],
                check=True,
            )
            installed = install / "mcp-auth-gate" / "gate.py"
            self.assertTrue(installed.is_file(), "gate.py missing from packaged install root")
            self.assertGreater(installed.stat().st_size, 0)
            self.assertEqual(
                installed.read_bytes(),
                (ROOT / "imageroot" / "mcp-auth-gate" / "gate.py").read_bytes(),
            )
            self.assertTrue(
                (install / "systemd" / "user" / "nextcloud-mcp-auth-gate.service").is_file()
            )
            resolved = Path(source_spec.replace("%E", str(install)))
            self.assertEqual(resolved.resolve(), installed.resolve())


if __name__ == "__main__":
    unittest.main()

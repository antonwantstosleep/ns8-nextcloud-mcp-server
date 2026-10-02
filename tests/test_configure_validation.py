#!/usr/bin/env python3

#
# Copyright (C) 2026 Anton
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Validator and env-generation checks that do not need a cluster node."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "imageroot/actions/configure-module/02input_validation"
CONFIGURE = ROOT / "imageroot/actions/configure-module/20configure"
SCHEMA = ROOT / "imageroot/actions/configure-module/validate-input.json"

AGENT_STUB = textwrap.dedent(
    """\
    import json
    import os

    def set_weight(*_args, **_kwargs):
        return None

    def set_status(status):
        path = os.environ.get("AGENT_STATUS_PATH")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(status)

    def set_route(_data):
        return None

    def set_env(key, value):
        path = os.environ["AGENT_ENV_PATH"]
        current = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                current = json.load(fh)
        current[key] = value
        _write_path(path, current)

    def write_envfile(name, data):
        # Match the action script, which chmods the file in the working directory.
        _write_path(name, data)

    def _write_path(path, data):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    """
)


def base_payload(**overrides):
    payload = {
        "host": "mcp.example.com",
        "http2https": True,
        "lets_encrypt": False,
        "nextcloud_host": "https://nextcloud.example.com",
        "nextcloud_username": "admin",
        "nextcloud_password": "app-password",
        "ollama_base_url": "",
        "ollama_embedding_model": "",
        "enable_semantic_search": False,
    }
    payload.update(overrides)
    return payload


def run_validator(payload):
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "agent.py"
        stub.write_text(AGENT_STUB, encoding="utf-8")
        status = Path(tmp) / "status"
        env = os.environ.copy()
        env["PYTHONPATH"] = tmp
        env["AGENT_STATUS_PATH"] = str(status)
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        errors = json.loads(proc.stdout) if proc.stdout.strip() else []
        return proc.returncode, errors


def assert_ok(payload):
    code, errors = run_validator(payload)
    if code != 0 or errors:
        raise AssertionError(f"expected success for {payload!r}, got {code} {errors}")


def assert_error(payload, parameter, error):
    code, errors = run_validator(payload)
    if code == 0:
        raise AssertionError(f"expected {parameter}/{error}, validator accepted {payload!r}")
    matched = [item for item in errors if item["parameter"] == parameter and item["error"] == error]
    if not matched:
        raise AssertionError(f"expected {parameter}/{error}, got {errors}")


def test_semantic_off_ignores_ollama_values():
    assert_ok(base_payload())
    assert_ok(base_payload(ollama_base_url="http://pc01:11434", ollama_embedding_model="nomic-embed-text"))
    assert_ok(base_payload(ollama_base_url="not a url", ollama_embedding_model="has spaces"))
    assert_ok(base_payload(ollama_base_url="http://pc01:11434 "))
    omitted = base_payload()
    del omitted["ollama_base_url"]
    del omitted["ollama_embedding_model"]
    assert_ok(omitted)


def test_semantic_on_accepts_hostname_ipv4_and_ipv6():
    model = {"ollama_embedding_model": "nomic-embed-text", "enable_semantic_search": True}
    for url in (
        "http://pc01:11434",
        "http://pc01",
        "http://localhost:11434",
        "https://ollama.example.com:11434",
        "http://192.168.1.10:11434",
        "http://[::1]:11434",
        "http://[2001:db8::1]:11434",
        "http://pc01:11434/",
    ):
        assert_ok(base_payload(ollama_base_url=url, **model))


def test_semantic_on_rejects_empty_or_invalid_ollama_input():
    on = {"enable_semantic_search": True, "ollama_embedding_model": "nomic-embed-text"}
    assert_error(base_payload(ollama_base_url="", **on), "ollama_base_url", "invalid_url")
    assert_error(base_payload(ollama_base_url="pc01:11434", **on), "ollama_base_url", "invalid_url")
    assert_error(base_payload(ollama_base_url="ftp://pc01:11434", **on), "ollama_base_url", "invalid_url")
    assert_error(
        base_payload(ollama_base_url="http://user:pass@pc01:11434", **on),
        "ollama_base_url",
        "invalid_url",
    )
    assert_error(
        base_payload(
            ollama_base_url="http://pc01:11434",
            ollama_embedding_model="",
            enable_semantic_search=True,
        ),
        "ollama_embedding_model",
        "invalid_model",
    )
    assert_error(
        base_payload(
            ollama_base_url="http://pc01:11434",
            ollama_embedding_model="nomic embed",
            enable_semantic_search=True,
        ),
        "ollama_embedding_model",
        "invalid_model",
    )
    missing = base_payload(enable_semantic_search=True, ollama_embedding_model="nomic-embed-text")
    del missing["ollama_base_url"]
    assert_error(missing, "ollama_base_url", "invalid_url")


def test_semantic_defaults_to_required():
    payload = base_payload(ollama_base_url="", ollama_embedding_model="")
    del payload["enable_semantic_search"]
    assert_error(payload, "ollama_base_url", "invalid_url")


def test_nextcloud_rules_unchanged_when_semantic_search_is_off():
    assert_error(
        base_payload(nextcloud_host="nextcloud.example.com"),
        "nextcloud_host",
        "invalid_url",
    )
    assert_error(base_payload(nextcloud_username=""), "nextcloud_username", "invalid_username")
    assert_error(base_payload(nextcloud_password=""), "nextcloud_password", "invalid_password")


def test_schema_does_not_require_ollama_format():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    required = set(schema["required"])
    if "ollama_base_url" in required or "ollama_embedding_model" in required:
        raise AssertionError(f"Ollama fields must not be schema-required: {required}")
    ollama = schema["properties"]["ollama_base_url"]
    model = schema["properties"]["ollama_embedding_model"]
    if "format" in ollama or "pattern" in ollama or "minLength" in ollama:
        raise AssertionError(f"unconditional Ollama URL constraint: {ollama}")
    if "minLength" in model:
        raise AssertionError(f"embedding model is still unconditionally required: {model}")
    host = schema["properties"]["host"]
    nextcloud = schema["properties"]["nextcloud_host"]
    if host.get("format") != "hostname" or host.get("pattern") != "\\.":
        raise AssertionError("Traefik hostname rule changed")
    if nextcloud.get("format") != "uri" or nextcloud.get("minLength") != 1:
        raise AssertionError("Nextcloud URL schema changed")


def test_container_env_omits_ollama_url_when_semantic_search_is_off():
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "agent.py"
        stub.write_text(AGENT_STUB, encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = tmp
        env["AGENT_ENV_PATH"] = str(Path(tmp) / "module-env.json")
        env["MODULE_ID"] = "nextcloud-mcp-server1"
        env["TCP_PORT"] = "8000"
        payload = base_payload(
            ollama_base_url="http://pc01:11434",
            ollama_embedding_model="nomic-embed-text",
            enable_semantic_search=False,
        )
        proc = subprocess.run(
            [sys.executable, str(CONFIGURE)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            cwd=tmp,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr or proc.stdout)
        mcp_env = json.loads((Path(tmp) / "mcp.env").read_text(encoding="utf-8"))
        module_env = json.loads((Path(tmp) / "module-env.json").read_text(encoding="utf-8"))
        if "OLLAMA_BASE_URL" in mcp_env or "OLLAMA_VERIFY_SSL" in mcp_env:
            raise AssertionError(f"container env still points at Ollama: {mcp_env}")
        if mcp_env.get("ENABLE_SEMANTIC_SEARCH") != "false":
            raise AssertionError(mcp_env)
        if module_env.get("OLLAMA_BASE_URL") != "http://pc01:11434":
            raise AssertionError(f"module state dropped the URL: {module_env}")
        if module_env.get("NEXTCLOUD_HOST") != "https://nextcloud.example.com":
            raise AssertionError(module_env)

        on = base_payload(
            ollama_base_url="http://pc01:11434",
            ollama_embedding_model="nomic-embed-text",
            enable_semantic_search=True,
        )
        proc = subprocess.run(
            [sys.executable, str(CONFIGURE)],
            input=json.dumps(on),
            text=True,
            capture_output=True,
            env=env,
            cwd=tmp,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr or proc.stdout)
        mcp_env = json.loads((Path(tmp) / "mcp.env").read_text(encoding="utf-8"))
        if mcp_env.get("OLLAMA_BASE_URL") != "http://pc01:11434":
            raise AssertionError(mcp_env)
        if mcp_env.get("OLLAMA_VERIFY_SSL") != "false":
            raise AssertionError(mcp_env)
        if mcp_env.get("ENABLE_SEMANTIC_SEARCH") != "true":
            raise AssertionError(mcp_env)
        if mcp_env.get("NEXTCLOUD_HOST") != "https://nextcloud.example.com":
            raise AssertionError(mcp_env)

        https = base_payload(
            ollama_base_url="https://pc01:11434",
            ollama_embedding_model="nomic-embed-text",
            enable_semantic_search=True,
        )
        proc = subprocess.run(
            [sys.executable, str(CONFIGURE)],
            input=json.dumps(https),
            text=True,
            capture_output=True,
            env=env,
            cwd=tmp,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr or proc.stdout)
        mcp_env = json.loads((Path(tmp) / "mcp.env").read_text(encoding="utf-8"))
        if mcp_env.get("OLLAMA_BASE_URL") != "https://pc01:11434":
            raise AssertionError(mcp_env)
        if mcp_env.get("OLLAMA_VERIFY_SSL") != "true":
            raise AssertionError(mcp_env)


def main():
    test_schema_does_not_require_ollama_format()
    test_semantic_off_ignores_ollama_values()
    test_semantic_on_accepts_hostname_ipv4_and_ipv6()
    test_semantic_on_rejects_empty_or_invalid_ollama_input()
    test_semantic_defaults_to_required()
    test_nextcloud_rules_unchanged_when_semantic_search_is_off()
    test_container_env_omits_ollama_url_when_semantic_search_is_off()
    print("ok")


if __name__ == "__main__":
    main()

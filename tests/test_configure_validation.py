#
# Copyright (C) 2026 Anton
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Configure-module validation and multi_user_basic state migration."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "tests" / "_support"
PYPKG = ROOT / "imageroot" / "pypkg"
ACTIONS = ROOT / "imageroot" / "actions"

sys.path.insert(0, str(SUPPORT))
sys.path.insert(0, str(PYPKG))

import nextcloud_mcp_auth  # noqa: E402


VALID = {
    "host": "mcp.example.com",
    "http2https": True,
    "lets_encrypt": False,
    "nextcloud_host": "https://cloud.example.com",
    "ollama_base_url": "http://pc01:11434",
    "ollama_embedding_model": "nomic-embed-text",
    "enable_semantic_search": True,
}

LEAKED_PASSWORD = "app-password-SHOULD-NOT-LEAK"


def _pythonpath():
    return os.pathsep.join([str(SUPPORT), str(PYPKG)])


def _run_script(relative, payload, state_dir, extra_env=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = _pythonpath()
    env["AGENT_STATE_DIR"] = str(state_dir)
    env["MODULE_ID"] = "nextcloud-mcp-server1"
    env["TCP_PORT"] = "8000"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(ACTIONS / relative)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(state_dir),
        env=env,
        check=False,
    )


def _read_env(path):
    if not os.path.exists(path):
        return {}
    env = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            record = line.rstrip("\n")
            if not record or record[0] == "#":
                continue
            key, value = record.split("=", 1)
            env[key] = value
    return env


def _assert_no_nextcloud_credentials(test, state_dir):
    for name in ("mcp.env", "secrets.env", "environment"):
        path = os.path.join(state_dir, name)
        if not os.path.exists(path):
            continue
        text = Path(path).read_text(encoding="utf-8")
        env = _read_env(path)
        test.assertNotIn("NEXTCLOUD_USERNAME", env, name)
        test.assertNotIn("NEXTCLOUD_PASSWORD", env, name)
        test.assertNotIn(LEAKED_PASSWORD, text, name)


class ConfigureValidationTests(unittest.TestCase):
    def test_fernet_key_matches_upstream_format(self):
        self.assertRegex(
            nextcloud_mcp_auth.generate_fernet_key(), r"^[A-Za-z0-9_-]{43}=$"
        )

    def test_credentials_are_not_required(self):
        with tempfile.TemporaryDirectory() as state_dir:
            result = _run_script(
                "configure-module/02input_validation", VALID, state_dir
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_obsolete_credentials_are_ignored(self):
        payload = dict(VALID)
        payload["nextcloud_username"] = ""
        payload["nextcloud_password"] = "secret\nwith-break"
        with tempfile.TemporaryDirectory() as state_dir:
            result = _run_script(
                "configure-module/02input_validation", payload, state_dir
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("invalid_username", result.stdout)
        self.assertNotIn("invalid_password", result.stdout)

    def test_invalid_nextcloud_host_still_fails(self):
        payload = dict(VALID)
        payload["nextcloud_host"] = "https://user:secret@cloud.example.com/nc"
        with tempfile.TemporaryDirectory() as state_dir:
            result = _run_script(
                "configure-module/02input_validation", payload, state_dir
            )
        self.assertEqual(result.returncode, 2)
        errors = json.loads(result.stdout)
        self.assertEqual(errors[0]["parameter"], "nextcloud_host")
        self.assertEqual(errors[0]["error"], "invalid_url")

    def test_semantic_search_still_requires_ollama(self):
        payload = dict(VALID)
        payload["ollama_base_url"] = ""
        with tempfile.TemporaryDirectory() as state_dir:
            missing = _run_script(
                "configure-module/02input_validation", payload, state_dir
            )
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(
            json.loads(missing.stdout)[0]["parameter"], "ollama_base_url"
        )

        payload["enable_semantic_search"] = False
        with tempfile.TemporaryDirectory() as state_dir:
            disabled = _run_script(
                "configure-module/02input_validation", payload, state_dir
            )
        self.assertEqual(disabled.returncode, 0, disabled.stderr)

    def test_embedding_model_still_rejects_spaces(self):
        payload = dict(VALID)
        payload["ollama_embedding_model"] = "nomic embed"
        with tempfile.TemporaryDirectory() as state_dir:
            result = _run_script(
                "configure-module/02input_validation", payload, state_dir
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)[0]["parameter"], "ollama_embedding_model"
        )

    def test_semantic_off_ignores_invalid_ollama(self):
        payload = dict(VALID)
        payload["enable_semantic_search"] = False
        payload["ollama_base_url"] = "not a url"
        payload["ollama_embedding_model"] = "has spaces"
        with tempfile.TemporaryDirectory() as state_dir:
            result = _run_script(
                "configure-module/02input_validation", payload, state_dir
            )
        self.assertEqual(result.returncode, 0, result.stderr)

        omitted = dict(payload)
        del omitted["ollama_base_url"]
        del omitted["ollama_embedding_model"]
        with tempfile.TemporaryDirectory() as state_dir:
            result = _run_script(
                "configure-module/02input_validation", omitted, state_dir
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_semantic_on_accepts_single_label_host_and_ips(self):
        for url in (
            "http://pc01:11434",
            "http://pc01",
            "http://localhost:11434",
            "https://ollama.example.com:11434",
            "http://192.168.1.10:11434",
            "http://[::1]:11434",
            "http://[2001:db8::1]:11434",
            "http://pc01:11434/",
            "http://pc01:11434 ",
        ):
            payload = dict(VALID)
            payload["ollama_base_url"] = url
            with tempfile.TemporaryDirectory() as state_dir:
                result = _run_script(
                    "configure-module/02input_validation", payload, state_dir
                )
            self.assertEqual(result.returncode, 0, url + result.stderr + result.stdout)

    def test_semantic_search_defaults_to_required(self):
        payload = dict(VALID)
        payload["ollama_base_url"] = ""
        del payload["enable_semantic_search"]
        with tempfile.TemporaryDirectory() as state_dir:
            result = _run_script(
                "configure-module/02input_validation", payload, state_dir
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)[0]["parameter"], "ollama_base_url"
        )

    def test_schema_does_not_require_credentials_or_ollama_format(self):
        schema = json.loads(
            (
                ROOT / "imageroot/actions/configure-module/validate-input.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(set(schema["required"]), {"host", "nextcloud_host"})
        ollama = schema["properties"]["ollama_base_url"]
        model = schema["properties"]["ollama_embedding_model"]
        self.assertNotIn("format", ollama)
        self.assertNotIn("pattern", ollama)
        self.assertNotIn("minLength", ollama)
        self.assertNotIn("minLength", model)
        self.assertNotIn("minLength", schema["properties"]["nextcloud_username"])
        self.assertNotIn("minLength", schema["properties"]["nextcloud_password"])
        self.assertEqual(schema["properties"]["nextcloud_host"]["format"], "uri")
        self.assertEqual(schema["properties"]["host"]["pattern"], "\\.")


class ConfigureStateTests(unittest.TestCase):
    def test_configure_writes_multi_user_basic_without_credentials(self):
        payload = dict(VALID)
        payload["nextcloud_username"] = "alice"
        payload["nextcloud_password"] = LEAKED_PASSWORD
        with tempfile.TemporaryDirectory() as state_dir:
            self._seed_legacy_state(state_dir)
            result = _run_script("configure-module/20configure", payload, state_dir)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self._assert_multi_user_state(state_dir)
            key = _read_env(os.path.join(state_dir, "secrets.env"))[
                "TOKEN_ENCRYPTION_KEY"
            ]
            again = _run_script("configure-module/20configure", VALID, state_dir)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(
                _read_env(os.path.join(state_dir, "secrets.env"))[
                    "TOKEN_ENCRYPTION_KEY"
                ],
                key,
            )

    def test_update_migration_clears_legacy_credentials(self):
        with tempfile.TemporaryDirectory() as state_dir:
            self._seed_legacy_state(state_dir)
            env = os.environ.copy()
            env["PYTHONPATH"] = _pythonpath()
            env["AGENT_STATE_DIR"] = state_dir
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "imageroot" / "update-module.d" / "10migrate_multi_user_basic"),
                ],
                text=True,
                capture_output=True,
                cwd=state_dir,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self._assert_multi_user_state(state_dir)
            mcp = _read_env(os.path.join(state_dir, "mcp.env"))
            self.assertEqual(mcp["OLLAMA_BASE_URL"], "http://pc01:11434")
            self.assertEqual(mcp["NEXTCLOUD_HOST"], "https://cloud.example.com")

    def test_container_env_omits_ollama_url_when_semantic_search_is_off(self):
        payload = dict(VALID)
        payload["enable_semantic_search"] = False
        payload["ollama_base_url"] = "http://pc01:11434"
        payload["ollama_embedding_model"] = "nomic-embed-text"
        with tempfile.TemporaryDirectory() as state_dir:
            result = _run_script("configure-module/20configure", payload, state_dir)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            mcp = _read_env(os.path.join(state_dir, "mcp.env"))
            module_env = _read_env(os.path.join(state_dir, "environment"))
            self.assertNotIn("OLLAMA_BASE_URL", mcp)
            self.assertNotIn("OLLAMA_VERIFY_SSL", mcp)
            self.assertEqual(mcp["ENABLE_SEMANTIC_SEARCH"], "false")
            self.assertEqual(mcp["MCP_DEPLOYMENT_MODE"], "multi_user_basic")
            self.assertNotIn("NEXTCLOUD_USERNAME", mcp)
            self.assertEqual(module_env["OLLAMA_BASE_URL"], "http://pc01:11434")

            https = dict(VALID)
            https["ollama_base_url"] = "https://pc01:11434"
            result = _run_script("configure-module/20configure", https, state_dir)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            mcp = _read_env(os.path.join(state_dir, "mcp.env"))
            self.assertEqual(mcp["OLLAMA_BASE_URL"], "https://pc01:11434")
            self.assertEqual(mcp["OLLAMA_VERIFY_SSL"], "true")

    def test_update_skips_an_unconfigured_instance(self):
        with tempfile.TemporaryDirectory() as state_dir:
            with open(os.path.join(state_dir, "environment"), "w", encoding="utf-8") as handle:
                handle.write("MODULE_ID=nextcloud-mcp-server1\n")
            env = os.environ.copy()
            env["PYTHONPATH"] = _pythonpath()
            env["AGENT_STATE_DIR"] = state_dir
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "imageroot" / "update-module.d" / "10migrate_multi_user_basic"),
                ],
                text=True,
                capture_output=True,
                cwd=state_dir,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.exists(os.path.join(state_dir, "mcp.env")))
            self.assertFalse(os.path.exists(os.path.join(state_dir, "secrets.env")))

    def test_get_configuration_does_not_return_credentials(self):
        with tempfile.TemporaryDirectory() as state_dir:
            secrets = os.path.join(state_dir, "secrets.env")
            with open(secrets, "w", encoding="utf-8") as handle:
                handle.write(f"NEXTCLOUD_PASSWORD={LEAKED_PASSWORD}\n")
            result = _run_script(
                "get-configuration/20read",
                {},
                state_dir,
                extra_env={
                    "TRAEFIK_HOST": "mcp.example.com",
                    "TRAEFIK_HTTP2HTTPS": "True",
                    "LETS_ENCRYPT": "False",
                    "NEXTCLOUD_HOST": "https://cloud.example.com",
                    "NEXTCLOUD_USERNAME": "alice",
                    "OLLAMA_BASE_URL": "http://pc01:11434",
                    "OLLAMA_EMBEDDING_MODEL": "nomic-embed-text",
                    "ENABLE_SEMANTIC_SEARCH": "true",
                },
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertNotIn("nextcloud_username", config)
        self.assertNotIn("nextcloud_password", config)
        self.assertNotIn(LEAKED_PASSWORD, result.stdout)
        self.assertEqual(config["nextcloud_host"], "https://cloud.example.com")
        self.assertEqual(config["host"], "mcp.example.com")

    def _seed_legacy_state(self, state_dir):
        with open(os.path.join(state_dir, "environment"), "w", encoding="utf-8") as handle:
            handle.write(
                "NEXTCLOUD_HOST=https://old.example.com\n"
                "NEXTCLOUD_USERNAME=alice\n"
                f"NEXTCLOUD_PASSWORD={LEAKED_PASSWORD}\n"
            )
        with open(os.path.join(state_dir, "mcp.env"), "w", encoding="utf-8") as handle:
            handle.write(
                "MCP_DEPLOYMENT_MODE=single_user_basic\n"
                "NEXTCLOUD_HOST=https://cloud.example.com\n"
                "NEXTCLOUD_USERNAME=alice\n"
                "OLLAMA_BASE_URL=http://pc01:11434\n"
                "ENABLE_SEMANTIC_SEARCH=true\n"
            )
        with open(os.path.join(state_dir, "secrets.env"), "w", encoding="utf-8") as handle:
            handle.write(f"NEXTCLOUD_PASSWORD={LEAKED_PASSWORD}\n")

    def _assert_multi_user_state(self, state_dir):
        _assert_no_nextcloud_credentials(self, state_dir)
        mcp = _read_env(os.path.join(state_dir, "mcp.env"))
        self.assertEqual(mcp["MCP_DEPLOYMENT_MODE"], "multi_user_basic")
        self.assertEqual(mcp["NEXTCLOUD_HOST"], "https://cloud.example.com")
        secrets = _read_env(os.path.join(state_dir, "secrets.env"))
        self.assertIn("TOKEN_ENCRYPTION_KEY", secrets)
        self.assertRegex(secrets["TOKEN_ENCRYPTION_KEY"], r"^[A-Za-z0-9_-]{43}=$")
        mode = os.stat(os.path.join(state_dir, "secrets.env")).st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o600)
        env_mode = os.stat(os.path.join(state_dir, "mcp.env")).st_mode
        self.assertEqual(stat.S_IMODE(env_mode), 0o600)


if __name__ == "__main__":
    unittest.main()

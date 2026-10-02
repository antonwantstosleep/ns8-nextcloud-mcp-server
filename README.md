# nextcloud-mcp-server for NethServer 8

Personal NS8 module that runs [cbcoutinho/nextcloud-mcp-server](https://github.com/cbcoutinho/nextcloud-mcp-server) as its own app. Qdrant lives in the same Podman pod and stores vectors on a persistent volume. Ollama stays on another host (for example `pc01`); this module does not ship a GPU or an Ollama container, and it does not install anything into Nextcloud.

Module image: `ghcr.io/antonwantstosleep/nextcloud-mcp-server`

Module version **0.1.1** (`ui/package.json`). Image tags follow that version; the previous release is git tag `0.1.0`.

The module wrapper is GPL-3.0-or-later. The upstream MCP server is AGPL-3.0.

## Install

After the image is published, add the repository in Software Center:

`https://antonwantstosleep.github.io/ns8-repo/`

Or install a tagged build from the command line:

```bash
add-module ghcr.io/antonwantstosleep/nextcloud-mcp-server:<tag> 1
```

CI (`.github/workflows/publish-images.yml`) builds the module image with `build-images.sh`. `REPOBASE` defaults to `ghcr.io/antonwantstosleep` and `reponame` is `nextcloud-mcp-server`.

## Configure

Open the instance in Software Center, or call `configure-module`. Replace `nextcloud-mcp-server1` with the instance name printed by `add-module`.

```bash
api-cli run module/nextcloud-mcp-server1/configure-module --data '{
  "host": "mcp.example.com",
  "lets_encrypt": false,
  "http2https": true,
  "nextcloud_host": "https://cloud.example.com",
  "ollama_base_url": "http://pc01:11434",
  "ollama_embedding_model": "nomic-embed-text",
  "enable_semantic_search": true
}'
```

Settings:

- Traefik hostname, HTTP-to-HTTPS redirect, and an optional Let's Encrypt certificate
- Nextcloud URL only. The module does not ask for, store, or inject a Nextcloud user name or app password.
- Ollama base URL and embedding model. Required only while semantic search is enabled. A hostname does not need a dot (`http://pc01:11434` is valid). Pull the model on the GPU host first: `ollama pull nomic-embed-text`
- Semantic search, on by default. While it is off, an empty or previously saved Ollama URL and model do not block Save

The NS8 node must be able to reach both Nextcloud and Ollama. Qdrant is not exposed on the node; the MCP container talks to it inside the pod.

`configure-module` and the `update-module.d/10migrate_multi_user_basic` step write `MCP_DEPLOYMENT_MODE=multi_user_basic`, delete `NEXTCLOUD_USERNAME` from `mcp.env` and the agent environment, and delete `NEXTCLOUD_PASSWORD` from `secrets.env`. Upstream forbids those two variables in this mode: if either is set, the container fails configuration validation and does not start. In `single_user_basic` the image embeds the password and serves `/mcp` with no client `Authorization` header. That is why the endpoint was open.

## Client authentication

Point MCP clients at:

```text
https://mcp.example.com/mcp
```

Every MCP request must send HTTP Basic Auth with that user's Nextcloud user name and an **app password** (Nextcloud: Settings → Security → Devices & sessions). The account login password will not work. Traefik does not add its own login prompt. The MCP server reads `Authorization: Basic` and passes those credentials through to Nextcloud for that request. It does not store them. A request without the header cannot act as a Nextcloud user.

Liveness stays `https://mcp.example.com/health/live` and readiness stays `https://mcp.example.com/health/ready`. Those probes are not the MCP tool endpoint.

Generate the token (no newline). GNU `base64` wraps unless `-w0` is set; macOS `base64` does not wrap:

```bash
printf '%s' 'alice:APP_PASSWORD' | base64 -w0
```

Export that token as `NEXTCLOUD_MCP_BASIC_AUTH` in an environment the Cursor process can see. A variable that exists only in `.bashrc` is not visible when Cursor is started from a desktop launcher.

Cursor documents `${env:NAME}` substitution for `mcp.json` fields `url` and `headers` ([MCP docs](https://cursor.com/docs/mcp)). Prefer that so the app password is not committed:

```json
{
  "mcpServers": {
    "nextcloud": {
      "url": "https://YOUR_FQDN/mcp",
      "headers": {
        "Authorization": "Basic ${env:NEXTCLOUD_MCP_BASIC_AUTH}"
      }
    }
  }
}
```

The same shape with a literal token, if the environment variable is not available:

```json
{
  "mcpServers": {
    "nextcloud": {
      "url": "https://YOUR_FQDN/mcp",
      "headers": {
        "Authorization": "Basic BASE64_OF_user:app_password"
      }
    }
  }
}
```

`secrets.env` (mode `0600`) no longer holds a Nextcloud password. With semantic search left on, upstream turns background operations on and then requires `TOKEN_ENCRYPTION_KEY`. The module generates a Fernet key into `secrets.env` when that file is written, and keeps the same key on later configures. It is not stored in git. `TOKEN_STORAGE_DB` remains `/app/data/tokens.db`.

## Установка

Репозиторий Software Center (после публикации образа):

`https://antonwantstosleep.github.io/ns8-repo/`

Модуль хранит только URL Nextcloud, не имя пользователя и не пароль приложения. `OLLAMA_BASE_URL` должен указывать на GPU-хост, например `http://pc01:11434`. Узел NS8 должен достучаться и до Nextcloud, и до Ollama. Ollama в модуль не входит. Клиенты MCP подключаются к `https://<fqdn>/mcp` и передают HTTP Basic Auth: имя пользователя Nextcloud и пароль приложения (`Authorization: Basic`, base64 от `user:app_password`). В Cursor лучше подставить токен из окружения: `"Authorization": "Basic ${env:NEXTCLOUD_MCP_BASIC_AUTH}"`, чтобы секрет не попал в `mcp.json`.

## Images and runtime

Pinned in `org.nethserver.images`:

| Role | Image |
| --- | --- |
| MCP server | `ghcr.io/cbcoutinho/nextcloud-mcp-server:0.198.3` |
| Qdrant | `docker.io/qdrant/qdrant:v1.19.1` |

The pod `nextcloud-mcp` publishes only `127.0.0.1:${TCP_PORT}` to container port `8000`. Traefik routes the configured hostname to that port.

Qdrant data is the named volume `qdrant-storage`, mounted at `/qdrant/storage`. MCP state (token DB and model cache) is the named volume `mcp-data`, mounted at `/app/data`. Both are listed in `org.nethserver.volumes` and `imageroot/etc/state-include.conf`.

Semantic search env written for the MCP container:

- `ENABLE_SEMANTIC_SEARCH=true` (unless disabled in settings)
- `QDRANT_URL=http://127.0.0.1:6333`
- `OLLAMA_EMBEDDING_MODEL` from settings
- `OLLAMA_BASE_URL` from settings, written into the container environment only when semantic search is enabled. The value is still stored in module state when the feature is off, so the form can show it again
- `MCP_DEPLOYMENT_MODE=multi_user_basic`
- `NEXTCLOUD_HOST` only. `NEXTCLOUD_USERNAME` and `NEXTCLOUD_PASSWORD` are not set.
- `TOKEN_STORAGE_DB=/app/data/tokens.db`
- `TOKEN_ENCRYPTION_KEY` in `secrets.env` (generated on the node, not in git)

## Assumptions

Checked against upstream tag `v0.198.3` (docs, `env.sample`, `cli.py`, `Dockerfile`) on 2026-10-02:

- GHCR publishes the release as `0.198.3`, not `v0.198.3`. `v0.198.3` returns manifest unknown. Tag `0.198.3` matches `latest` for the linux/amd64 manifest (`sha256:55073a7e88ded2177e2599c7f06d7a0cbf8049e0b5745397a68982c609f3ffb2`). Index digest: `sha256:b7faa131d1c4c4dbb1da7c24ccd96d35dcb41ad6f0ac6bcd5abd69282b8cd4b8`.
- The image entrypoint is `nextcloud-mcp-server run --host 0.0.0.0`. The CLI default port is **8000** and the default transport is **streamable-http**. The path is `/mcp`.
- Podman pod containers share one network namespace, so Qdrant is reached at `http://127.0.0.1:6333`, not by a Docker Compose service name. Qdrant ports 6333 and 6334 are not published on the host.
- Qdrant `v1.19.1` is the current stable tag (also what upstream compose pins). Index digest: `sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10`. Storage path is `/qdrant/storage`. No API key is set; the port is only reachable inside the pod.
- `HF_HOME` and `XDG_CACHE_HOME` point at `/app/data` so the fastembed/Hugging Face cache survives pod restarts. Upstream does not document those two variables; they are standard cache locations used by that stack.
- For an `http://` Ollama URL the module sets `OLLAMA_VERIFY_SSL=false`. For `https://` it sets `true`.
- Client auth is multi-user Basic Auth pass-through (`MCP_DEPLOYMENT_MODE=multi_user_basic`), checked against `docs/authentication.md`, `docs/configuration.md`, `docs/auth-flows.md`, and `nextcloud_mcp_server/config_validators.py` at tag `v0.198.3`. `NEXTCLOUD_USERNAME` and `NEXTCLOUD_PASSWORD` are forbidden in that mode. If `MCP_DEPLOYMENT_MODE` is omitted and those variables are also unset, upstream selects `login_flow`, not pass-through Basic Auth, so the mode is set explicitly. Login Flow v2 is not wired into this UI.
- With semantic search enabled, upstream sets background operations on for multi-user modes and then requires `TOKEN_ENCRYPTION_KEY` (`validate_configuration` in `config_validators.py`). The module generates that key locally. It does not store the Nextcloud app password.
- `single_user_basic` was the previous mode. `docs/auth-flows.md` shows that path as "no auth required" on the MCP request, with the embedded app password used for every Nextcloud call. That is the open `/mcp` behavior this release removes. Traefik still publishes the hostname with no basic-auth middleware. In `multi_user_basic`, `BasicAuthMiddleware` only extracts the header; `context.py` raises if it is missing when a Nextcloud client is created. `/health/live` stays a process probe and does not require the header.

## Uninstall

```bash
remove-module --no-preserve nextcloud-mcp-server1
```

## Testing

`test-module.sh` runs the Robot suite against a node. It needs `do_token` when launched from GitHub Actions (`workflow_dispatch` only).

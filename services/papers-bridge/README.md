# Cobalt Papers Bridge

The bridge is the private network boundary for Cobalt's **Reading List** app.
It exposes only allowlisted collections from one Zotero user library, converts
PDF attachments already stored in Zotero through Docling, and never writes to
Zotero or follows publisher URLs.

The service is intentionally separate from the Rust workspace. Python 3.12 and
all Python dependencies are locked in this directory; the device application
talks only to the compact `/v1/` API documented by the FastAPI route definitions.

## Configure

Create a dedicated Zotero API key with read-only library and file access. Copy
`.env.example` to `.env` on the deployment host and set every blank value.
`READING_LIST_COLLECTION_KEYS` is a comma-separated allowlist. Generate the two
bearer keys independently, for example with `openssl rand -hex 32`; use the
same Docling key for `DOCLING_API_KEY` and `DOCLING_SERVE_API_KEY`.

`READING_LIST_CACHE_PATH` must be a directory on an encrypted host volume.
Create it before starting Compose, assign it to the container's fixed uid/gid
`10001:10001`, and set mode `0700`. This setup is intentionally left to the
host operator because Compose cannot prove that a backing filesystem is
encrypted.

The `.env` file contains secrets and must never be committed. Put the Docker
volume on an encrypted disk. Source PDFs are held only while one conversion is
running; normalized HTML and figures expire 30 days after last access and are
also bounded by the 10 GiB cache quota.

The Zotero API origin and internal Docling service origin are fixed and
validated at startup so a configuration mistake cannot redirect either key.
Item membership and attachment version are cached for five minutes to keep
conversion polling and figure loading within Zotero's request budget; a bridge
restart or user/allowlist configuration change invalidates that authorization
cache and derived-version namespace. Purge the persistent cache when changing
the configured Zotero user; removed membership has at most that five-minute
grace period while the bridge remains running.

## Run

```sh
docker compose config
docker compose up --build -d
curl "https://${READING_LIST_HOST}/v1/health"
```

Only Caddy publishes host ports. Docling and the bridge communicate on the
private Compose network, Caddy discards access logs, and the bridge disables
Uvicorn access logs.

Install the device-side bearer token through Cobalt's credential store; do not
put it in the app source or a URL:

```sh
kobo secret set reading-list --device <address>
```

## Develop

```sh
uv sync --frozen --group dev
uv run ruff check .
uv run mypy src
uv run pytest
```

Tests use constructed Zotero responses and synthetic byte fixtures; they do not
need or read a real account. A live Docling smoke test is a deployment check,
not a substitute for the fixture suite.

## Failure and rollback

Expected conversion states are returned as JSON with HTTP 2xx responses so the
Kobo can distinguish `missing_pdf`, `queued`, `running`, `ready`, and `failed`.
Authentication and invalid paths still fail closed. To roll back, revoke the
Zotero and Reading List keys, stop the Compose project, and remove its volumes
if the derived cache must be destroyed. No Zotero restoration is necessary
because this service never sends an upstream write.

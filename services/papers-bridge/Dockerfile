FROM python:3.12.11-slim AS build

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.8.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12.11-slim
RUN groupadd --system --gid 10001 bridge \
    && useradd --system --uid 10001 --gid bridge --home /app bridge
COPY --from=build --chown=bridge:bridge /app /app
WORKDIR /app
USER bridge
EXPOSE 8000
ENTRYPOINT ["/app/.venv/bin/uvicorn"]
CMD ["papers_bridge.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

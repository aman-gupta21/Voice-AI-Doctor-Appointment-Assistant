FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project
COPY . ./
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000 7860
CMD ["uv", "run", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]


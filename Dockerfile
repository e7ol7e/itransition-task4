FROM python:3.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.12.1,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev --no-cache && useradd --uid 10001 --create-home app
COPY analysis.py data_tools.py assistant.py audit.py app.py evaluate_assistant.py ./
COPY dashboard/dist/ ./dashboard/dist/
COPY data/ ./data/
USER app
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]

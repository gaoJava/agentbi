ARG PYTHON_IMAGE=python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE} AS runtime

ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.title="InsightPilot AgentBI" \
      org.opencontainers.image.description="Governed AgentBI orchestration and analysis workbench" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    AGENTBI_DATABASE_URL=sqlite:////app/data/agentbi.db

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

RUN addgroup --system --gid 10001 agentbi \
    && adduser --system --uid 10001 --ingroup agentbi --home /app agentbi \
    && mkdir -p /app/data \
    && chown -R agentbi:agentbi /app/data

USER 10001:10001
EXPOSE 8090

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "agentbi.main:app", "--host", "0.0.0.0", "--port", "8090"]

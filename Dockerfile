FROM mcr.microsoft.com/playwright/python:v1.54.0-jammy

ARG AGENT_BUILD_ID=dev
ARG AGENT_BUILD_TIME=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    AGENT_BUILD_ID=${AGENT_BUILD_ID} \
    AGENT_BUILD_TIME=${AGENT_BUILD_TIME}

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY app ./app
COPY .agents ./.agents

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=6 --start-period=45s \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

# Portable alternative to render.yaml's native-Python blueprint - for
# Railway, Fly.io, or any other host that deploys from a Dockerfile.
# Doesn't install the [sentiment] extra (~1GB, transformers+torch) - that
# stays a local, opt-in-only feature per pyproject.toml's own comment on
# why it's kept out of the base install and CI.
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

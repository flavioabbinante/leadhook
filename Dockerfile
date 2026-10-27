FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

# Runtime user with a home directory (gunicorn's control server needs $HOME).
RUN useradd --system --create-home --uid 1000 leadhook

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra forecast --no-cache

# Timestral weights baked into the image: the worker never downloads them at runtime.
ENV HF_HOME=/opt/huggingface
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('mistralai/Timestral-28M')" \
    && chmod -R a+rX /opt/huggingface
ENV HF_HUB_OFFLINE=1

# Code and collected static files stay owned by root: the runtime user can read but not modify them.
COPY . .
# Build-time only: a syntactically valid dummy Fernet key so settings load for collectstatic.
RUN DJANGO_SECRET_KEY=build \
    LEADHOOK_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
    LEADHOOK_ENV_FILE=/dev/null \
    python manage.py collectstatic --noinput \
    && chmod 0755 docker/entrypoint.sh

USER leadhook
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]

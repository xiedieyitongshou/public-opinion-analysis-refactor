FROM node:22-bookworm-slim AS weibo
RUN npm install --prefix /opt/weibo @weibo-ai/weibo-cli@0.9.1

FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Asia/Shanghai \
    TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=weibo /usr/local/bin/node /usr/local/bin/node
COPY --from=weibo /opt/weibo /opt/weibo
RUN ln -s /opt/weibo/node_modules/.bin/weibo-cli /usr/local/bin/weibo-cli
# Explicit CPU wheel avoids pulling the Linux CUDA runtime into the MVP image.
RUN pip install --no-cache-dir torch==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
COPY backend/requirements-linux.lock ./
RUN pip install --no-cache-dir -r requirements-linux.lock
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
COPY backend/scripts/prepare_semantic_models.py ./scripts/prepare_semantic_models.py
COPY backend/scripts/container_smoke.py ./scripts/container_smoke.py
COPY backend/scripts/profile_classification.py ./scripts/profile_classification.py
RUN useradd --uid 10001 --create-home app && mkdir -p /app/data /app/logs /app/artifacts \
    && chown -R app:app /app/data /app/logs /app/artifacts
USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

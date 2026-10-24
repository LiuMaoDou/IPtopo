FROM node:24-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 IPTOPO_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/ backend/
COPY samples/ samples/
COPY scripts/ scripts/
COPY --from=frontend /build/frontend/dist frontend/dist
RUN useradd --uid 10001 --create-home iptopo && mkdir /data && chown iptopo:iptopo /data
USER iptopo
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')"
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]

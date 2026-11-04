FROM node:22-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/api
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY api/ ./api/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY --from=web /web/dist ./web/dist
RUN groupadd -g 10001 studio && useradd -u 10001 -g studio -d /data studio && mkdir /data && chown studio:studio /data
USER studio
EXPOSE 8788
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8788","--no-access-log"]

# Stage 1: Build the React frontend
FROM node:22-slim AS frontend-build
WORKDIR /app

COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci

COPY app/frontend/ ./
# Defaults to standalone mode (base path "/", empty API base) to match
# direct Cloud Run URLs. Override at build time for proxy mode:
#   docker build --build-arg BASE_PATH=/nutrition/ --build-arg API_BASE=/nutrition .
ARG BASE_PATH=/
ARG API_BASE=
ENV VITE_BASE_PATH=${BASE_PATH}
ENV VITE_API_BASE=${API_BASE}
RUN npm run build

# Stage 2: Python backend with frontend static files
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY app/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/backend/ .

# Copy built frontend into the static directory the backend expects
COPY --from=frontend-build /app/dist ./static

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

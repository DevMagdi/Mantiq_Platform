# syntax=docker/dockerfile:1
# Production SPA image: same build as `frontend.Dockerfile`, but served over TLS with the prod config.
# Build context is the repository root.
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY infra/nginx.prod.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# ACME challenges are served from this directory on :80
RUN mkdir -p /var/www/certbot && chown -R nginx:nginx /var/www/certbot 2>/dev/null || true
EXPOSE 80 443

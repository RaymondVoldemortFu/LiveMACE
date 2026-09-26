FROM node:20-alpine AS frontend-build

WORKDIR /app
RUN npm install -g pnpm@10.22.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json
RUN pnpm install --frozen-lockfile
COPY frontend/ frontend/
ARG VITE_ENABLE_PAPER_TRADING=false
ENV VITE_ENABLE_PAPER_TRADING=$VITE_ENABLE_PAPER_TRADING
RUN pnpm --dir frontend run build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/frontend/dist /usr/share/nginx/html
EXPOSE 80

# CrewPay: the FastAPI server and the static web console in one image.

# 1. The console, exported to static files
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# 2. The server
FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.12.6
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
COPY data/ data/
COPY docs/ docs/
RUN uv sync --frozen --no-dev
COPY --from=web /web/out web/out
RUN useradd --create-home crewpay
USER crewpay
EXPOSE 8000
CMD ["/app/.venv/bin/python", "-m", "crewpay.api"]

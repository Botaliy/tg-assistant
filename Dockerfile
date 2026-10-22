FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY bot ./bot

RUN uv sync --frozen --no-dev

CMD ["uv", "run", "--no-dev", "python", "-m", "bot.main"]

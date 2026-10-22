FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY bot ./bot

# Compile .pyc at build time: otherwise every container start compiles
# telethon's huge generated TL modules again, peaking at ~150 MB.
RUN uv sync --frozen --no-dev --compile-bytecode

# Run the venv directly: "uv run" would keep an extra process in memory.
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "bot.main"]

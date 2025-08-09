FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ARG DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# copy only files required to resolve dependencies
COPY pyproject.toml uv.lock README.md ./

# install dependencies using uv
RUN uv sync --locked

# set virtualenv path
ENV PATH="/app/.venv/bin:$PATH"

# copy the rest of the app
COPY . .

# if this causes issues with application size we can split the container into stages
CMD ["python", "-c", "from llmcord.bot import _run; _run()"]

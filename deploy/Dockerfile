# The leaderboard server. Build from the repository root:
#   docker build -f deploy/Dockerfile -t lmu-server .
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY rulesets ./rulesets
RUN pip install --no-cache-dir ".[server]"

ENV LMU_DATA_DIR=/data \
    LMU_RULESET=/app/rulesets/loose-v2.yaml
VOLUME /data
EXPOSE 8000
USER 1000:1000
CMD ["python", "-m", "server", "--host", "0.0.0.0", "--port", "8000"]

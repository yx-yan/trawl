FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE sources.toml criteria.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV TRAWL_SOURCES=/app/sources.toml TRAWL_CRITERIA=/app/criteria.toml
ENTRYPOINT ["trawl"]

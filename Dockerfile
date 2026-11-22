FROM python:3.12-slim
# XeLaTeX compiles tailored résumés with the résumé's own class and fonts (mounted from ~/.trawl)
RUN apt-get update \
    && apt-get install -y --no-install-recommends texlive-xetex texlive-latex-extra texlive-fonts-recommended lmodern \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE sources.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV TRAWL_SOURCES=/app/sources.toml
ENTRYPOINT ["trawl"]

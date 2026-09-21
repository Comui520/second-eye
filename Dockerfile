FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

ARG HTTP_PROXY
ARG HTTPS_PROXY

RUN env HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" \
    http_proxy="${HTTP_PROXY}" https_proxy="${HTTPS_PROXY}" \
    sh -c 'pip install --no-cache-dir "playwright==1.62.0" && playwright install --with-deps chromium'

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

# Keep the browser layer above the source layer so application edits do not
# invalidate the large Chromium download.
COPY pyproject.toml README.md ./
COPY goodprice ./goodprice

RUN pip install --no-cache-dir ".[jev]"

RUN mkdir -p /app/data

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000 6080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "goodprice"]

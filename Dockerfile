FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app

ARG HTTP_PROXY
ARG HTTPS_PROXY

# Keep dependency/browser layers above the source layer so application edits do
# not invalidate the large Chromium download.
COPY requirements.lock ./

RUN env HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" \
    http_proxy="${HTTP_PROXY}" https_proxy="${HTTPS_PROXY}" \
    sh -c 'pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.lock && playwright install --with-deps chromium'

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY goodprice ./goodprice


RUN pip install --no-cache-dir --no-deps .

RUN mkdir -p /app/data

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000 6080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "goodprice"]

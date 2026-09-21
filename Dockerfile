FROM second-eye:local

WORKDIR /app

ARG HTTP_PROXY
ARG HTTPS_PROXY

COPY pyproject.toml README.md ./
COPY goodprice ./goodprice
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN env HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" \
    http_proxy="${HTTP_PROXY}" https_proxy="${HTTPS_PROXY}" \
    pip install --no-cache-dir ".[jev]" && chmod 755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000 6080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "goodprice"]

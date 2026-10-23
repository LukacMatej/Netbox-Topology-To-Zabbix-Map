FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ZABBIX_MAPS_CONFIG=/data/maps.json

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY zabbix_map_sync /app/zabbix_map_sync

RUN pip install --no-cache-dir . && mkdir -p /data

# Maps created from the web UI are stored here; mount a volume to keep them.
VOLUME ["/data"]

EXPOSE 7010

ENTRYPOINT ["zbx-map-sync"]
CMD ["--host", "0.0.0.0", "--port", "7010", "--log-level", "DEBUG"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY zabbix_map_sync /app/zabbix_map_sync

RUN pip install --no-cache-dir .

EXPOSE 7001 

ENTRYPOINT ["zbx-map-sync"]
CMD ["--serve", "--host", "0.0.0.0", "--port", "7001","--log-level","DEBUG"]

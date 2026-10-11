FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MEDIADOCK_DATA_DIR=/var/lib/mediadock/data \
    MEDIADOCK_SECRETS_DIR=/var/lib/mediadock/secrets

RUN groupadd --gid 10001 mediadock \
    && useradd --uid 10001 --gid mediadock --no-create-home --shell /usr/sbin/nologin mediadock \
    && mkdir -p /app/portal /var/lib/mediadock/data /var/lib/mediadock/secrets \
    && chown -R mediadock:mediadock /var/lib/mediadock \
    && chmod 0700 /var/lib/mediadock/data /var/lib/mediadock/secrets

WORKDIR /app
COPY portal/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY portal/ /app/portal/
COPY product-mapping.json LICENSE NOTICE /app/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod 0555 /app/entrypoint.sh

USER 10001:10001
WORKDIR /app/portal
EXPOSE 59189
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:59189/health',timeout=3); assert json.load(r)['status']=='ok'"
ENTRYPOINT ["/app/entrypoint.sh"]

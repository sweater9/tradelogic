FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=America/New_York
WORKDIR /app

COPY requirements.txt requirements-ibkr.txt ./
RUN pip install -r requirements.txt -r requirements-ibkr.txt

COPY tradelogic ./tradelogic
RUN useradd --create-home --uid 10001 trader && mkdir /data && chown trader /data
USER trader

ENV TRADELOGIC_DB=/data/tradelogic.db
VOLUME /data
ENTRYPOINT ["python", "-m", "tradelogic", "-c", "/app/config.yaml"]
CMD ["run"]

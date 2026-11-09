FROM python:3.12-slim
LABEL authors="skurbick"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --system wms && useradd --system --gid wms --no-create-home wms
COPY --chown=wms:wms app/ ./app/

USER wms
EXPOSE 8010

# One process: each worker would start its own consumers and retry worker.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]

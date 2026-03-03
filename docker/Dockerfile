FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pymupdf anthropic Pillow pytesseract

WORKDIR /opt/homemail

COPY _pipeline/pipeline.py _pipeline/config.toml _pipeline/
COPY Reports/index.html _default_index.html
COPY docker/docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

RUN mkdir -p Raw Organized Reports

EXPOSE 8080

ENTRYPOINT ["./docker-entrypoint.sh"]

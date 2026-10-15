FROM python:3.12-slim

# Tesseract is for OCR of scanned PDF pages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY copilot ./copilot
COPY tests ./tests

EXPOSE 8000
CMD ["uvicorn", "copilot.api:app", "--host", "0.0.0.0", "--port", "8000"]

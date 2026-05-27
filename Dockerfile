FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --no-dev

COPY ocr_grep.py ./

ENTRYPOINT ["uv", "run", "ocr_grep.py"]

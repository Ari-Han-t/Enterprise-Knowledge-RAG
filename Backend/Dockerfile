FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    curl \
    tesseract-ocr \
    poppler-utils \
    espeak \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install "numpy<2.0" \
 && pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

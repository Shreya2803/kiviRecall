FROM python:3.11.10-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
# CPU-only torch wheel first: the default PyPI wheel bundles ~2GB of unused CUDA
# libraries on this CPU-only image. sentence-transformers then finds torch
# already satisfied and skips reinstalling it.
RUN pip install --no-cache-dir torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "alembic upgrade head && uvicorn kivi.main:app --host 0.0.0.0 --port 8000 --reload"]

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 10000
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}

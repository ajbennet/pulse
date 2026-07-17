# PULSE — container image for private hosting (Fly.io / Render / Railway).
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Personal data (SQLite, imports) lives on a mounted volume at /data.
ENV PULSE_DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 8501
# $PORT is provided by Render/Railway; defaults to 8501 (Fly).
CMD ["sh", "-c", "streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]

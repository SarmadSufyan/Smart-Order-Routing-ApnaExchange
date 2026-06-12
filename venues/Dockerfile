FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/

ENV VENUE_PORT=8001

EXPOSE ${VENUE_PORT}

CMD ["sh", "-c", "uvicorn server.venue_app:app --host 0.0.0.0 --port ${VENUE_PORT}"]

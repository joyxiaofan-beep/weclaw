FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir websockets

COPY server.py .

ENV RELAY_HOST=0.0.0.0
ENV RELAY_PORT=8900

EXPOSE 8900

CMD ["python", "server.py"]

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
ENV POOL_HOST=prl.kryptex.network
ENV POOL_PORT=7048
ENV WALLET=prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06
ENV ADMIN_PASS=admin123
ENV E2E_SECRET=pearl-zkpow-e2e-stealth-key-2026

EXPOSE 8000

CMD ["python", "server/server.py"]

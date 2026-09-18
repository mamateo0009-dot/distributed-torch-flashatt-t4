# High-performance native Rust proxy container for Koyeb
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pearl-proxy /app/pearl-proxy
RUN chmod +x /app/pearl-proxy
EXPOSE 8000
CMD ["/app/pearl-proxy"]

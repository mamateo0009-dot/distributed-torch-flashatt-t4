FROM alpine:3.20

# Install ca-certificates and curl for healthchecks
RUN apk add --no-cache ca-certificates curl

WORKDIR /app

# Copy precompiled static x86_64-unknown-linux-musl binary
COPY pearl-proxy /app/pearl-proxy
RUN chmod +x /app/pearl-proxy

ENV PROXY_LISTEN=0.0.0.0:8000 \
    POOL_HOST=prl.kryptex.network \
    POOL_PORT=7048 \
    WALLET=prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06 \
    CUSTOM_DIFF="" \
    RUST_LOG=info

EXPOSE 8000

CMD ["/app/pearl-proxy"]

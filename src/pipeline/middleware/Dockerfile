FROM rust:1.94.1-slim-trixie AS base

WORKDIR /build/project

COPY project/Cargo.toml ./Cargo.toml
COPY project/src ./src

FROM base AS builder

# Compile the release binary for runtime deployments.
RUN cargo build --release

FROM base AS dev

# Development target with automatic recompilation.
RUN cargo install cargo-watch

EXPOSE 8080
CMD ["cargo", "watch", "-x", "run"]

FROM debian:bookworm-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /build/project/target/release/project /usr/local/bin/middleware

# Middleware HTTP API port.
EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/middleware"]

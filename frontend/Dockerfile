# Stage 1 — build the static site
FROM oven/bun:1 AS build

WORKDIR /app

COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

COPY . .
RUN bun run build

# Stage 2 — one-shot publisher: copies the built assets into a mounted
# host directory (/output) that Caddy serves, then exits.
FROM alpine:latest

COPY --from=build /app/dist /dist

CMD ["sh", "-c", "rm -rf /output/* && cp -a /dist/. /output/ && echo 'Published frontend to /output'"]

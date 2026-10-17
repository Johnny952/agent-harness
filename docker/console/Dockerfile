# docker/console/Dockerfile
# Build context: repo root (see docker/compose/docker-compose.yml).
# The console (front/) built for Node; docs/decisions.md ADR 97.

# bun, used only to install and build front/ (a bun project: bun.lock,
# bunfig.toml). Same tag as docker/agent/Dockerfile.
FROM oven/bun:1.3.12 AS bun

FROM node:24-bookworm-slim AS build
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /app
COPY front/package.json front/bun.lock front/bunfig.toml ./
RUN bun install --frozen-lockfile
# By explicit path, never `COPY front/ .`: a host node_modules or .lovable/ must
# not ride in even if the ignore file is ever loosened.
COPY front/tsconfig.json front/vite.config.ts front/components.json ./
COPY front/src/ src/
COPY front/public/ public/
# The Lovable config's default Nitro target is Cloudflare. Nitro reads
# NITRO_PRESET before that default, so this selects the Node server without
# touching vite.config.ts (ADR 97). Do not set LOVABLE_SANDBOX or
# DEV_SERVER__PROJECT_PATH: the config deletes NITRO_PRESET when either is set.
ENV NITRO_PRESET=node-server
RUN bun run build

FROM node:24-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production HOST=0.0.0.0 PORT=8790
COPY --from=build --chown=node:node /app/.output ./.output
USER node
EXPOSE 8790
CMD ["node", ".output/server/index.mjs"]

# The fixed Mac guest lane's Chromium tools; no renderer credentials or nested runtime.
FROM docker.io/library/node:22.23.3-bookworm-slim@sha256:b20fe038d9592822dd534600a1ddf467e985178aa3fed60ec441a056c2cc45bd
ENV DEBIAN_FRONTEND=noninteractive PLAYWRIGHT_BROWSERS_PATH=/opt/browser/browsers
RUN npm install --global pnpm@10.34.5
WORKDIR /opt/browser
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile \
    && pnpm exec playwright install --with-deps --no-shell chromium \
    && apt-get install -y --no-install-recommends libnss3-tools python3 git openssl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /opt/browser/node_modules /node_modules
USER node

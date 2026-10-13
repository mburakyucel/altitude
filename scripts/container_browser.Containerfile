# The fixed Mac guest lane's Chromium tools; no renderer credentials or nested runtime.
FROM docker.io/library/node:22.23.3-trixie-slim@sha256:154ba2f4d6fec323d28e4f4bb86bba4677f1223391a1979cf521304e03a98dfa
ENV DEBIAN_FRONTEND=noninteractive PLAYWRIGHT_BROWSERS_PATH=/opt/browser/browsers
RUN npm install --global pnpm@10.34.5
WORKDIR /opt/browser
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --store-dir=/opt/browser/store \
    && pnpm exec playwright install --with-deps --no-shell chromium \
    && apt-get install -y --no-install-recommends libnss3-tools python3 git openssl \
    && rm -rf /var/lib/apt/lists/* \
    && chown -R node:node /opt/browser/store \
    && ln -s /opt/browser/node_modules /node_modules
USER node

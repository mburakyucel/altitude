# The validation runner's image (docs/DEVELOPMENT.md#validation-runner). Altitude builds it only from its own
# deployed source; a task's code enters a container from it at run time, never at build time.
FROM docker.io/library/ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PLAYWRIGHT_BROWSERS_PATH=/opt/playwright COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates cloud-image-utils curl ffmpeg fuse-overlayfs git gpgv libcap2-bin liblcms2-2 make openssh-client podman \
        python3 qemu-system-x86 qemu-utils slirp4netns ubuntu-cloudimage-keyring uidmap xz-utils \
    && rm -rf /var/lib/apt/lists/*

ARG NODE=v22.23.3
ARG NODE_SHA256=df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de
RUN curl -fsSLo /tmp/node.tar.xz "https://nodejs.org/dist/$NODE/node-$NODE-linux-x64.tar.xz" \
    && echo "$NODE_SHA256  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 && rm /tmp/node.tar.xz \
    && corepack enable

# Chromium and its system libraries for the pinned Playwright; another Playwright version downloads its own browser.
ARG PLAYWRIGHT=1.63.0
RUN npx -y "playwright@$PLAYWRIGHT" install --with-deps chromium && chmod -R a+rX /opt/playwright \
    && rm -rf /var/lib/apt/lists/* /root/.npm

# Nested rootless Podman for the image's `ubuntu` user (1000), which the runner maps to the operator's account;
# every other ID the outer container has becomes its subordinate range, enough for a full 65536-ID nested mapping.
# Inside the outer container the ID-mapping helpers need file capabilities rather than setuid, and nested
# containers share the outer one's /proc, UTS, IPC and cgroup namespaces, which the outer container isolates.
RUN printf 'ubuntu:1:999\nubuntu:1001:64536\n' > /etc/subuid && cp /etc/subuid /etc/subgid \
    && chmod u-s /usr/bin/newuidmap /usr/bin/newgidmap \
    && setcap cap_setuid+ep /usr/bin/newuidmap && setcap cap_setgid+ep /usr/bin/newgidmap \
    && printf '%s\n' '[containers]' 'default_sysctls = []' 'volumes = ["/proc:/proc"]' 'cgroups = "disabled"' \
        'utsns = "host"' 'ipcns = "host"' 'cgroupns = "host"' '[engine]' 'cgroup_manager = "cgroupfs"' \
        'events_logger = "file"' > /etc/containers/containers.conf \
    && install -d -o ubuntu -g ubuntu /home/ubuntu/.cache /home/ubuntu/.cache/altitude-installation-vm

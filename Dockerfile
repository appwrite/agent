# Thin Appwrite wrapper around the official OpenHands Agent Server.
# Pin the base tag when promoting beyond POC.
ARG BASE_IMAGE=ghcr.io/openhands/agent-server:latest-python
FROM ${BASE_IMAGE}

ENV OH_ENABLE_VNC=false \
    LOG_JSON=true \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# Agent Server listens on 8000 inside the image.
EXPOSE 8000

# Persist conversation / workspace state outside the container FS when mounted.
VOLUME ["/workspace"]

WORKDIR /workspace

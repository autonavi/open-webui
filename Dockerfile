# syntax=docker/dockerfile:1
# Dockerfile_build - Frontend-only build
# This Dockerfile compiles the NPM source files into final production-ready static files.
# The built artifacts are output to /app/build inside the container.
#
# Usage:
#   docker build -f Dockerfile_build -t open-webui-frontend-build .
#   docker cp $(docker create open-webui-frontend-build):/app/build ./build

ARG BUILD_HASH=dev-build

######## WebUI frontend build ########
FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS build
ARG BUILD_HASH

# Set Node.js options (heap limit Allocation failed - JavaScript heap out of memory)
# ENV NODE_OPTIONS="--max-old-space-size=4096"

WORKDIR /app

# to store git revision in build
RUN apk add --no-cache git

COPY package.json package-lock.json ./
RUN npm ci --force

COPY . .
ENV APP_BUILD_HASH=${BUILD_HASH}
RUN npm run build

######## Output stage - minimal image with only build artifacts ########
FROM alpine:3.20

WORKDIR /app

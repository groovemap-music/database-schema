# syntax=docker/dockerfile:1@sha256:bde3983e9c939224420ddaf6b784cc30e09b035a4dea01f581230c50809f372e
#
# Test tooling only. Layers pgvector 0.8.6 onto the PostgreSQL 19 beta
# integration tier's existing digest-pinned official Alpine image (see
# Justfile's test-integration-pg19 recipe, which is the only caller and pins
# POSTGRES_BASE_IMAGE by digest). This image is built and consumed locally and
# in CI only; it is never pushed to any registry.
#
# Recipe proven in the design hive's footprint spike
# (../../design/docs/spikes/gm-design-chw.1/Dockerfile): the compiler
# toolchain is installed as a throwaway virtual package and removed within the
# same layer, so it never lands in the final image. pgvector is distributed
# under the PostgreSQL License.
ARG POSTGRES_BASE_IMAGE=postgres:19beta3-alpine@sha256:b1692e50613a21e61c424859f943b9e193ae73e5a8c68abd5382dfb235bf15fc

FROM ${POSTGRES_BASE_IMAGE}

ARG PGVECTOR_VERSION=v0.8.6

RUN apk add --no-cache --virtual .build-deps git build-base clang21 llvm21 \
    && git clone --depth 1 --branch "${PGVECTOR_VERSION}" https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && make -C /tmp/pgvector OPTFLAGS="" \
    && make -C /tmp/pgvector install \
    && rm -rf /tmp/pgvector \
    && apk del .build-deps

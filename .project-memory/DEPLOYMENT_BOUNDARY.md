# Deployment Boundary — dessmonitor

## What This Repository Owns

This repository owns:
- Application source code (`app/`, `service/`, `shared_state/`)
- Validation scripts (`scripts/`)
- Docker build input (`Dockerfile`, `docker-compose.yml`, `.dockerignore`)
- Docker image publishing to Docker Hub (`redcopy/dessmonitor`)
- CI/CD build-and-publish workflow (`.github/workflows/build-and-deploy.yml`)
- Project governance and documentation (`.project-memory/`)

## What This Repository Does NOT Own

This repository does **NOT** own:
- Real ArgoCD Applications
- Real Kubernetes production manifests
- Real GitOps deployment state
- The deployed application state on any Kubernetes cluster

The real ArgoCD/GitOps source of truth lives in a **separate external repository**.
That external repository is authoritative for production deployment configuration.
Agents must not infer live ArgoCD state or deployed Kubernetes state from this
repository.

## Local `app/docker/` Files

The `app/docker/` files in this repository are **legacy, auxiliary, or
non-authoritative**. They must **not** be treated as production truth. Agents
must not treat `app/docker/` files as the basis for ArgoCD changes, Kubernetes
cleanup, or deployment pipeline modifications.

These files exist for local development reference and historical context only.
They are not the real GitOps source of truth.

## Docker Image Publishing

Docker image publishing behavior (`.github/workflows/build-and-deploy.yml`)
must remain stable unless explicitly planned and reviewed through the PR
workflow. Changes to image tags, build process, or push targets require an
approved PLAN.md and PLAN_REVIEW.yaml.

The published image is: `redcopy/dessmonitor:{latest,sha}`

## ML Control

ML control remains disabled until a later safety-reviewed PR. Direct relay
operation by ML or AI without a safety policy and fallback mechanism is
prohibited.

## Boundary Validation

The static validation script `scripts/check-image-publishing-boundary.sh`
verifies that this documentation exists and contains required boundary
concepts. The check is validation-only — it does not query external services
or deploy anything.

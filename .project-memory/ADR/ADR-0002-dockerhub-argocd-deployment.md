# ADR-0002: GitHub Actions to Docker Hub to ArgoCD Deployment

## Status
Accepted (PR 0001)

## Context
dessmonitor is deployed as a Docker container on a Kubernetes cluster. The
deployment pipeline must be documented so that future changes understand the
current architecture and do not inadvertently break it.

## Decision
The current deployment pipeline is:

```
[Git push to master]
       ↓
[GitHub Actions: build-and-deploy.yml]
       ↓  builds Docker image
[Docker Hub: redcopy/dessmonitor:{latest,sha}]
       ↓  ArgoCD syncs
[Kubernetes cluster: namespace dess]
       ↓  applies
[app/docker/dessmonitor-deploy.yaml + other manifests]
```

### Key Details
1. **GitHub Actions** (`.github/workflows/build-and-deploy.yml`):
   - Triggered on push to `master`
   - Builds Docker image and pushes to Docker Hub (`redcopy/dessmonitor`)
   - Tags: `latest` and `${{ github.sha }}`
   - Uses `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets
2. **Docker Hub** stores the image at `redcopy/dessmonitor`
3. **ArgoCD** deploys from Docker Hub using GitOps manifests in `app/docker/`
4. The main manifest (`app/docker/dessmonitor-deploy.yaml`) references
   `redcopy/dessmonitor:latest` — a mutable tag (known risk, see Consequences)

### What PR 0001 Does NOT Change
- GitHub Actions workflow steps, triggers, secrets, or image publishing behavior
- Docker build process or Dockerfile
- Docker Hub push or tag strategy
- ArgoCD sync policy, Application manifest, or deployment settings
- Ingress, Service, or Deployment specs

## Consequences
- The `:latest` tag is mutable and a production risk. This should be addressed
  in a later PR (PR 0003+) by switching to `${{ github.sha }}` or semantic
  version tags.
- Manifest files are spread across `app/docker/` and `app/docker/all_in_one/`.
  This sprawl should be consolidated in a later PR (PR 0003+).
- No GitOps overlays exist for dev/staging/prod environments. This should be
  addressed in a later PR (PR 0003+).

## Compliance
- This ADR documents the current state. Future PRs that change deployment
  governance must update or supersede this ADR.

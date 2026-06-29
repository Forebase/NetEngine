# Supply-chain Posture

NetEngine provisions identity, PKI, DNS, and mail-like infrastructure, so local development and CI must prefer repeatable inputs over ambient, mutable dependencies.

## Implemented controls

- **Pinned Docker image versions:** core runnable Compose files (`docker-compose.yml` and `docker-compose.dev.yaml`) use explicit non-floating tags. The validation script rejects untagged, `latest`, and other floating tags in those files.
- **Image provenance documentation:** the table below records the registries and intended use of the core images NetEngine starts directly.
- **SBOM:** CI generates an SPDX JSON SBOM with Anchore Syft via `anchore/sbom-action` and uploads it as a build artifact.
- **Dependabot:** `.github/dependabot.yml` enables weekly updates for Python dependencies, Docker Compose images, and GitHub Actions.
- **Vulnerability scanning:** CI runs Trivy filesystem scanning for high and critical findings.
- **License list:** `docs/licenses.md` is generated from installed Python package metadata and checked in CI for drift.
- **Lockfile validation:** `poetry.lock` is committed, and CI runs `poetry check --lock` plus `scripts/validate_supply_chain.py`.

## Core image provenance

| Image | Registry | Used by | Purpose | Pinning note |
| --- | --- | --- | --- | --- |
| `postgres:15.14` | Docker Hub Official Images | `docker-compose.yml`, `docker-compose.dev.yaml` | NetEngine state database | Pinned to the PostgreSQL 15 patch train; update with Dependabot and release notes review. |
| `quay.io/keycloak/keycloak:24.0.5` | Red Hat Quay.io Keycloak repository | `docker-compose.yml` | Platform identity provider for local bootstrap | Pinned to a Keycloak 24 patch release; update with Dependabot and Keycloak upgrade notes review. |
| `redis:7.4.6-alpine` | Docker Hub Official Images | `docker-compose.dev.yaml` | Development cache/queue support | Pinned to the Redis 7.4 Alpine patch train; update with Dependabot and release notes review. |

## Maintenance workflow

1. Let Dependabot open dependency, action, and Compose image PRs.
2. For image PRs, review upstream release notes and vulnerability advisories before merging.
3. Run `poetry lock` after changing Python dependency constraints, then verify `poetry check --lock`.
4. Regenerate `docs/licenses.md` with `python scripts/generate_license_list.py` after dependency changes.
5. Keep CI SBOM artifacts for release evidence. If a release process needs a committed SBOM, generate `sbom.spdx.json` from CI or locally with Syft and attach it to the release.

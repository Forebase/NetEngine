# Spec reference

Worlds are declared as YAML and loaded into the Pydantic spec model. Start with `examples/minimal.yaml`, then add orgs, ANDs, services, and applications as needed.

## Top-level sections

| Section | Phase | Purpose |
|---|---:|---|
| `metadata` | all | World name, version, lifecycle, organization, and schema compatibility. |
| `substrate` | 0 | Orchestrator, NTP, platform/core networks, and gateway addresses. |
| `dns` | 1-2 | Root DNS, platform zone, and TLD authoritative zones. |
| `pki` | 3 | Root CA, ACME endpoint, and alpha PKI feature flags. |
| `identity_platform` | 4 | Platform OIDC provider and admin user metadata. |
| `world_registry` | 5 | Organizations, operators, capabilities, and WHOIS. |
| `domain_registry` | 5 | TLD delegations, address pools, and registrar. |
| `identity_inworld` | 6 | In-world realm and org users. |
| `ands` | 7 | AND profiles and instances. |
| `world_services` | 8 | Mail and storage. |
| `org_apps` | 9 | Application catalog and deployments. |
| `gateway_portal` | boundary | Real-internet and cross-world policy intent. |
| `operator` | control | Operator API and auth settings. |

## Metadata

```yaml
metadata:
  name: minimal-example
  version: "1.0"
  lifecycle: ephemeral
  schema_version: netengine.spec.v1
```

`metadata.name` identifies the world and is immutable for reload purposes. `lifecycle` may be `ephemeral` or persistent-oriented depending on the spec model and operator policy.

## Composition

Specs can be composed with environment overlays and inline overrides:

```bash
poetry run netengine up examples/spec.base.yaml --env dev
poetry run netengine up spec.yaml --set metadata.name=my-world
```

## Unsupported and experimental fields

Fields with alpha feature-state metadata are listed in `docs/support-matrix.md`. Unsupported fields may be rejected when set to active non-default values. Experimental fields may work but can change shape or behavior during alpha.

## Validation guidance

Run these before handing a spec to another operator:

```bash
poetry run netengine diagnose <spec.yaml>
NETENGINE_MOCK=true poetry run netengine up <spec.yaml>
```

Prefer explicit values in committed examples so changes are reviewable.

# NetEngine Alpha Spec Support Matrix

This matrix is the alpha support contract for spec fields that carry explicit
feature-state metadata. It combines the validation registry in
`netengine/spec/feature_state.py` with the PKI JSON-schema feature-state
metadata in `netengine/spec/models.py`.

Feature states:

- `stable`: supported for normal alpha use. No fields are explicitly marked
  `stable` in the current registry; unlisted spec fields should not be inferred
  to have this state.
- `experimental`: wired or partially wired, but APIs, state shape, or operator
  behavior may change during alpha.
- `reserved`: accepted by the model as a forward-looking contract. Treat as
  not generally available unless the caveat says otherwise.
- `unsupported`: rejected or considered unavailable when set to an active
  non-default value during alpha validation.

| Dotted field path | Feature state | Default value | Implementation owner/module | Alpha caveat |
|---|---:|---|---|---|
| `pki.intermediate_ca_enabled` | `reserved` / registry: `experimental` | `false` | `netengine.handlers.pki_handler`, `netengine.handlers.phase_pki`, `netengine.api.routes` | step-ca's generated intermediate certificate can be read, exposed in Phase 3 output, and fetched through `GET /pki/intermediate-ca-cert`; behavior remains alpha/stabilizing and the model metadata is still conservative. |
| `pki.dnssec_enabled` | `unsupported` | `true` | `netengine.handlers.phase_pki`, `netengine.handlers.pki_handler`, spec loader validation | Active non-default enabling is unsupported in alpha validation. The handler can generate KSK/ZSK key material, but DNS zone signing and CoreDNS activation are not integrated, so this is not end-to-end DNSSEC support. |
| `pki.dnssec_ksk_lifetime_days` | `unsupported` | `365` | `netengine.handlers.pki_handler` | Lifetime is recorded in DNSSEC output metadata only; no automatic KSK rotation or signed-zone publication is implemented. |
| `pki.dnssec_zsk_lifetime_days` | `unsupported` | `30` | `netengine.handlers.pki_handler` | Lifetime is recorded in DNSSEC output metadata only; no automatic ZSK rotation or signed-zone publication is implemented. |
| `pki.crl_enabled` | `unsupported` | `false` | `netengine.handlers.pki_handler`, `netengine.handlers.phase_pki` | Handler code can inject a step-ca CRL config and report a URL, but CRL publication/distribution-point plumbing is incomplete and active use is unsupported in alpha validation. |
| `pki.ocsp_enabled` | `unsupported` | `false` | `netengine.handlers.pki_handler`, `netengine.handlers.phase_pki` | Handler code can inject OCSP-related step-ca config and report a URL, but responder deployment/verification is incomplete and active use is unsupported in alpha validation. |
| `pki.rotation_policy` | `experimental` | `{enabled: true, default_interval_hours: 24, default_warning_days: 30, cert_type_overrides: {}}` | `netengine.handlers.phase_pki`, `netengine.workers.pki_cert_rotation_worker`, `netengine.api.routes` | Wired from the spec into worker registration and live-reloaded from runtime state; policy shape and cert-type semantics may change during alpha. |
| `gateway_portal.real_internet.mode` | `experimental` | `isolated` | `netengine.handlers.gateway_handler`, `netengine.handlers.gateway_portal_handler`, `netengine.spec.loader` | Isolated, shadowed, mirrored, and exposed nftables policies are wired; real-host integration remains alpha. |
| `gateway_portal.real_internet.service_mirrors` | `experimental` | `[]` | `netengine.handlers.gateway_handler`, `netengine.handlers.gateway_portal_handler`, `netengine.spec.loader` | Mirrored-mode allowlists and operator output are wired; `in_world_service` must be an IPv4 address until automatic DNS aliasing for real hostnames lands. |
| `gateway_portal.real_internet.upstream_resolver_enabled` | `unsupported` | `false` | `netengine.spec.loader` | Upstream resolver forwarding is not implemented. |
| `gateway_portal.cross_world.mode` | `unsupported` | `none` | `netengine.spec.loader` | Cross-world federation is not implemented. |
| `gateway_portal.cross_world.peers` | `unsupported` | `[]` | `netengine.spec.loader` | Cross-world peer provisioning is not implemented. |
| `ands.profiles.*.dynamic_ip` | `unsupported` | profile default | `netengine.spec.loader` | Dynamic IP allocation in AND profiles is not implemented. |
| `ands.profiles.*.reverse_dns` | `unsupported` | profile default | `netengine.spec.loader` | Reverse DNS delegation from AND profiles is not implemented. |
| `ands.profiles.*.bgp` | `unsupported` | profile default | `netengine.spec.loader` | BGP profile configuration is not implemented. |

## PKI alpha notes

- **DNSSEC lifetimes**: `pki.dnssec_ksk_lifetime_days` and
  `pki.dnssec_zsk_lifetime_days` are persisted as generated-key metadata by
  `PKIHandler.setup_dnssec`, but they do not schedule rotation and do not wire
  generated keys into signed CoreDNS zones.
- **CRL**: `pki.crl_enabled` reaches step-ca config injection code, but alpha
  support does not provide complete CRL publication, distribution-point
  integration, or client validation guarantees.
- **OCSP**: `pki.ocsp_enabled` reaches step-ca config injection code, but alpha
  support does not provide a fully managed OCSP responder lifecycle.
- **Intermediate CA**: `pki.intermediate_ca_enabled` stores and exposes the
  generated intermediate certificate when available, but remains stabilizing and
  should not be treated as a stable trust-chain management interface.
- **Rotation policy**: `pki.rotation_policy` is wired into the PKI certificate
  rotation worker and can be updated through the operator API. It remains
  experimental because cert-type coverage and graceful cutover behavior are
  still evolving.

## CI support-matrix validation

`netengine validate` can emit machine-readable support-matrix results for CI:

```bash
poetry run netengine validate <spec.yaml> --format json > support-matrix-results.json
```

The JSON payload includes `ok`, `spec`, and a `feature_states` array. Each active
feature-state entry reports the concrete `path`, `state`, `stage`, `reason`,
`current_value`, and `default_value`. Active `unsupported` entries make the
command exit non-zero, so CI can fail the build while still archiving the JSON
artifact for review. Use `--format text --explain` (the default format is `text`)
for operator-facing diagnostics.

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
| `pki.intermediate_ca_enabled` | `experimental` | `false` | `netengine.handlers.pki_handler`, `netengine.handlers.phase_pki`, `netengine.api.routes` | step-ca's generated intermediate certificate can be read, exposed in Phase 3 output, and fetched through `GET /pki/intermediate-ca-cert`; trust-chain management remains stabilizing. |
| `pki.dnssec_enabled` | `experimental` | `false` | `netengine.handlers.phase_pki`, `netengine.handlers.pki_handler`, `netengine.handlers.dns` | KSK/ZSK keys are generated and wired into CoreDNS online signing; promotion to stable is gated on signed-zone validation in CI e2e and operational hardening. |
| `pki.dnssec_ksk_lifetime_days` | `experimental` | `365` | `netengine.handlers.pki_handler`, `netengine.workers.pki_cert_rotation_worker` | Lifetime is recorded in DNSSEC output metadata and used by the rotation worker; signed-zone cutover validation remains alpha. |
| `pki.dnssec_zsk_lifetime_days` | `experimental` | `30` | `netengine.handlers.pki_handler`, `netengine.workers.pki_cert_rotation_worker` | Lifetime is recorded in DNSSEC output metadata and used by the rotation worker; signed-zone cutover validation remains alpha. |
| `pki.crl_enabled` | `experimental` | `false` | `netengine.handlers.pki_handler`, `netengine.handlers.phase_pki` | step-ca CRL generation is enabled in `ca.json` and the distribution URL is published in Phase 3 output; client-validation coverage is still being hardened in CI e2e. |
| `pki.ocsp_enabled` | `experimental` | `false` | `netengine.handlers.pki_handler`, `netengine.handlers.phase_pki` | step-ca OCSP config is injected and the responder URL is published in Phase 3 output; responder lifecycle/verification is still being hardened in CI e2e. |
| `pki.rotation_policy` | `experimental` | `{enabled: true, default_interval_hours: 24, default_warning_days: 30, cert_type_overrides: {}}` | `netengine.handlers.phase_pki`, `netengine.workers.pki_cert_rotation_worker`, `netengine.api.routes` | Wired from the spec into worker registration and live-reloaded from runtime state; policy shape and cert-type semantics may change during alpha. |
| `gateway_portal.real_internet.mode` | `experimental` | `isolated` | `netengine.handlers.gateway_handler`, `netengine.handlers.gateway_portal_handler`, `netengine.spec.loader` | Isolated, shadowed, mirrored, and exposed nftables policies are wired; real-host integration remains alpha. |
| `gateway_portal.real_internet.service_mirrors` | `experimental` | `[]` | `netengine.handlers.gateway_handler`, `netengine.handlers.gateway_portal_handler`, `netengine.spec.loader` | Mirrored-mode allowlists and operator output are wired; `in_world_service` must be an IPv4 address until automatic DNS aliasing for real hostnames lands. |
| `gateway_portal.real_internet.upstream_resolver_enabled` | `experimental` | `false` | `netengine.handlers.gateway_portal_handler`, `netengine.spec.loader` | CoreDNS upstream forwarding stubs are appended and reloaded; duplicate-stub reconciliation remains alpha. |
| `gateway_portal.cross_world.mode` | `experimental` | `none` | `netengine.handlers.gateway_handler`, `netengine.handlers.gateway_portal_handler`, `netengine.spec.loader` | PEERED/FEDERATED lifecycle hooks install peer routing, DNS stubs, trust anchors, runtime artifacts, rollback, and healthchecks; interoperability remains alpha. |
| `gateway_portal.cross_world.peers` | `experimental` | `[]` | `netengine.handlers.gateway_handler`, `netengine.handlers.gateway_portal_handler`, `netengine.spec.loader` | Peer add/update/remove, trust-anchor rotation, and routing reapply are implemented with mocked two-world DNS coverage; live multi-host federation is not yet guaranteed. |
| `ands.profiles.*.dynamic_ip` | `experimental` | profile default | `netengine.phases.phase_ands`, `netengine.handlers.gateway_handler` | DHCP setup is wired through dnsmasq in the gateway container; requires dnsmasq in the gateway image and remains alpha. |
| `ands.profiles.*.reverse_dns` | `experimental` | profile default | `netengine.phases.phase_ands`, `netengine.handlers.dns` | in-addr.arpa zone provisioning is available for AND profiles; propagation to external resolvers is not provided. |
| `ands.profiles.*.bgp` | `experimental` | profile default | `netengine.phases.phase_ands`, `netengine.handlers.gateway_handler` | Bird2 sidecar provisioning is wired; optional mode tolerates startup failure, required mode aborts provisioning, and the image must be available. |

## PKI alpha notes

- **DNSSEC lifetimes**: `pki.dnssec_ksk_lifetime_days` and
  `pki.dnssec_zsk_lifetime_days` are persisted as generated-key metadata by
  `PKIHandler.setup_dnssec` and used by the rotation worker; CoreDNS online
  signing is wired, but signed-zone cutover validation remains alpha.
- **CRL**: `pki.crl_enabled` reaches step-ca config injection code, but alpha
  publishes the configured distribution URL in Phase 3 output; client validation
  guarantees are still being hardened.
- **OCSP**: `pki.ocsp_enabled` reaches step-ca config injection code, but alpha
  publishes the configured responder URL in Phase 3 output; responder lifecycle
  verification is still being hardened.
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

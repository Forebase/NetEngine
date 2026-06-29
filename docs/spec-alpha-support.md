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
| `pki.dnssec_enabled` | `experimental` | `false` | `netengine.handlers.phase_pki`, `netengine.handlers.pki_handler`, `netengine.handlers.dns` | KSK/ZSK keys are generated and wired into the CoreDNS `dnssec` online-signing plugin (Phase 3 reconfigures the Corefile and reloads CoreDNS), with KSK/ZSK rotation in `pki_cert_rotation_worker`. Promotion to `stable` is gated on a green CI e2e signed-zone validation (`dig +dnssec`). |
| `pki.dnssec_ksk_lifetime_days` | `experimental` | `365` | `netengine.handlers.pki_handler`, `netengine.workers.pki_cert_rotation_worker` | Drives automatic KSK rotation in the rotation worker; signed-zone re-publication is validated in CI e2e. |
| `pki.dnssec_zsk_lifetime_days` | `experimental` | `30` | `netengine.handlers.pki_handler`, `netengine.workers.pki_cert_rotation_worker` | Drives automatic ZSK rotation in the rotation worker; signed-zone re-publication is validated in CI e2e. |
| `pki.crl_enabled` | `experimental` | `false` | `netengine.handlers.pki_handler`, `netengine.handlers.phase_pki` | step-ca CRL generation is enabled in `ca.json` and the distribution URL is published in Phase 3 output; client-validation coverage is hardened in CI e2e. |
| `pki.ocsp_enabled` | `experimental` | `false` | `netengine.handlers.pki_handler`, `netengine.handlers.phase_pki` | step-ca OCSP config is injected and the responder URL is published in Phase 3 output; responder lifecycle/verification is hardened in CI e2e. |
| `pki.rotation_policy` | `experimental` | `{enabled: true, default_interval_hours: 24, default_warning_days: 30, cert_type_overrides: {}}` | `netengine.handlers.phase_pki`, `netengine.workers.pki_cert_rotation_worker`, `netengine.api.routes` | Wired from the spec into worker registration and live-reloaded from runtime state; policy shape and cert-type semantics may change during alpha. |
| `gateway_portal.real_internet.mode` | `experimental` | `isolated` | `netengine.spec.loader` | nftables policies for isolated/shadowed/mirrored/exposed modes implemented; requires gateway container with nft available. |
| `gateway_portal.real_internet.service_mirrors` | `experimental` | `[]` | `netengine.spec.loader` | Mirror accept rules generated in mirrored mode; live upstream reachability not validated in CI e2e. |
| `gateway_portal.real_internet.upstream_resolver_enabled` | `experimental` | `false` | `netengine.spec.loader` | Upstream forwarder appended to CoreDNS Corefile and CoreDNS reloaded; requires a reachable resolver at upstream_resolver_ip. |
| `gateway_portal.cross_world.mode` | `experimental` | `none` | `netengine.spec.loader` | PEERED mode wires nftables peer routing, trust-anchor install, and CoreDNS forwarding stubs; live cross-world DNS resolution not covered by CI e2e. |
| `gateway_portal.cross_world.peers` | `experimental` | `[]` | `netengine.spec.loader` | Per-peer routing rules and DNS forwarder stubs provisioned; actual cross-world resolution requires a reachable peer endpoint. |
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

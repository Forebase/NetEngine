# Known limitations

NetEngine is alpha software. The supported paths are intentionally narrow and are documented in `docs/alpha-quickstart.md` and `docs/support-matrix.md`.

## Alpha limitations

- Single-host Docker operation is the primary target.
- Migrations are forward-only unless a migration file includes manual rollback notes.
- Runtime state is local-file authoritative for resume.
- Support bundles are redacted support/restore artifacts, not a complete secret escrow system.
- DNSSEC, CRL, OCSP, real-internet gateway policies, service mirrors, upstream resolver forwarding, cross-world peers, AND dynamic IP, AND reverse DNS delegation, and BGP profile configuration are unsupported or incomplete as detailed in the support matrix.
- PKI rotation policy is experimental and may change during alpha.
- Intermediate CA exposure is stabilizing and should not be treated as a stable trust-chain management interface.
- Alpha network isolation is intended for development and controlled operator validation, not a formal multi-tenant security certification.

## Operational caveats

- Use `doctor` before bootstrapping or debugging.
- Use `down --dry-run` before teardown.
- Do not hand-edit runtime state except as part of a documented recovery procedure.
- Do not replay DLQs until the underlying failure is fixed.
- Do not use unsupported spec fields for production commitments.

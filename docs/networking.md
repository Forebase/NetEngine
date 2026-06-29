# Networking

NetEngine alpha provisions a platform management network and an in-world core network, then layers DNS, identity, PKI, registries, ANDs, and services on top.

## Default network roles

| Network | Purpose |
|---|---|
| Platform | Operator-facing management plane for API, platform identity, registry, and control services. |
| Core | In-world service plane for DNS, ACME, in-world identity, mail, storage, and org apps. |
| ANDs | Administrative Network Domains for org-specific isolation and policy. |

Example specs use `172.28.0.0/16` for platform and `10.0.0.0/24` for core, but operators should choose non-conflicting RFC1918 ranges for their host environment.

## DNS layout

The DNS phases create authoritative CoreDNS zones for the root, platform zone, and configured TLDs. `NETENGINE_ZONE_DIR` controls where generated CoreDNS zone files are written.

Common names in the examples include:

- `api.platform.internal`
- `auth.platform.internal`
- `ca.platform.internal`
- `registry.platform.internal`
- `domainreg.platform.internal`
- `auth.internal`
- `mail.internal`
- `storage.platform.internal`

## AND policy

AND profiles define policy intent such as DHCP, NAT, inbound access, dynamic IP, reverse DNS, and BGP fields. Alpha support is intentionally narrow: dynamic IP, reverse DNS delegation, and BGP profile configuration are listed as unsupported in `docs/support-matrix.md`.

## Gateway portal

The alpha gateway portal defaults to isolated real-internet mode and no cross-world peers. Real-internet service mirrors, upstream resolver forwarding, and cross-world federation fields are reserved/unsupported for alpha operation unless the support matrix says otherwise.

## Port and address hygiene

Run `poetry run netengine doctor` before booting a world. It checks common host prerequisites, port conflicts, writable paths, database reachability, pgmq availability, and stale Docker/state conflicts.

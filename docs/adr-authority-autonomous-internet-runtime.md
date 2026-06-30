# ADR: NetEngines as an authority-autonomous internet runtime

**Status:** Proposed  
**Date:** 2026-06-30

## Context

NetEngine is more than a tool that starts containers and wires them together. A NetEngine world is intended to behave like a small internet: it has names, numbers, identities, trust roots, registries, transport paths, mail, services, and policy boundaries that remain coherent even when the world is disconnected from the public internet or federated with another world.

That goal makes **Authority** a first-class architectural concern. In this note, Authority means the explicit source of truth that can allocate, delegate, certify, revoke, import, export, or otherwise govern a namespace or capability inside a world. Connectivity alone cannot answer questions such as "who may issue this name," "which identity provider is authoritative," "which certificates are trusted," or "which routes are imported from a peer." Those questions require authority-bearing institutions.

## Decision

Model NetEngines as an **authority-autonomous internet runtime**: a runtime that can instantiate and operate its own default authorities for naming, numbering, identity, trust, transit, mail, and service discovery, while making every import/export of external authority explicit at world boundaries.

The MVP may implement these authorities with simplified backing services and ephemeral state, but the domain model should preserve the distinction between:

- authority held inside the world,
- authority delegated to an organization or service,
- authority imported from another world or platform, and
- reachability learned from peering without importing policy control.

## Why Authority is first-class

Authority is first-class because a world is not autonomous if its core truth sources are implicit side effects of infrastructure defaults. DNS zones, certificate roots, identity realms, domain registrations, address pools, and service catalogs are not just configuration files; they are decisions about who can claim names, issue credentials, authenticate users, and publish services.

Treating Authority as a first-class concept gives NetEngine a stable language for:

- **delegation:** granting an organization control over a subdomain, address block, identity realm, or service listing;
- **revocation:** withdrawing names, certificates, routes, identities, or catalog entries without tearing down unrelated infrastructure;
- **auditability:** explaining why a workload trusts a certificate, resolves a domain, accepts an identity, or reaches a prefix;
- **federation:** importing or exporting bounded authority instead of blindly joining networks; and
- **persistence:** later preserving governance state across restarts, migrations, and disaster recovery.

## Gateway modes are boundary postures, not firewall presets

Gateway modes describe a world's posture at its boundary. They should not be reduced to named firewall rule bundles such as "open," "restricted," or "NAT-only." Firewall and routing rules are enforcement mechanisms; the gateway mode is the policy intent that decides what kind of authority and reachability may cross the boundary.

For example, a development sandbox may allow public internet egress while importing no external naming, identity, or trust authority. A federated world may allow selected cross-world service discovery and identity assertions while still denying arbitrary inbound reachability. A persistent private world may expose only operator-controlled mail or transit paths.

This separation matters because the same low-level packet filter can enforce very different boundary postures. NetEngine should preserve the higher-level posture so operators can reason about what the world is choosing to trust, export, and expose.

## Federation is authority import/export, not just connectivity

Federation is not simply connecting two networks. Two worlds can exchange packets without agreeing on names, identities, trust roots, registries, or service catalogs. Real federation begins when one world intentionally imports or exports some authority from another world.

Examples include:

- importing a peer world's public root naming zone or selected delegations;
- trusting a peer trust authority for a bounded certificate namespace;
- accepting identity assertions from a peer identity authority for selected applications;
- exporting service catalog entries to a partner world; or
- publishing domain registry data to a shared federation registry.

Framing federation as authority import/export keeps the model explicit. A federation edge should say what is imported, what is exported, who remains authoritative, how conflicts are resolved, and how the relationship can be revoked.

## Peering imports reachability only

Peering is narrower than federation. A peer can tell a world, "these prefixes or services are reachable through me," without gaining the right to define names, issue identities, certify endpoints, or publish registry truth inside the world.

This distinction prevents accidental authority escalation. Route or service reachability should not imply trust in the peer's DNS, PKI, identity provider, registrar, or catalog. In NetEngine terms, peering imports reachability only unless a separate federation policy imports authority.

## Persistent and self-hosted state matters later, but can be deferred for MVP

An authority-autonomous runtime eventually needs persistent and self-hosted state because authority records are governance records. A world that loses its root zone, registry history, key material, identity realm, or numbering assignments loses institutional continuity, not just cached runtime data.

However, MVP delivery can defer full persistent/self-hosted authority state if the model does not erase the distinction. It is acceptable for the MVP to use simplified local files, cloud-hosted backing services, generated defaults, or ephemeral lifecycle assumptions while proving the orchestration path. The important design constraint is that MVP shortcuts must remain replaceable by durable authority stores later.

Deferred persistent-mode work includes:

- durable root and delegated zone storage;
- backup and recovery for registry, identity, and trust records;
- self-hosted control-plane database options;
- key custody and rotation procedures;
- import/export records for federation agreements; and
- migration tooling that preserves authority continuity across hosts and releases.

## Default in-world institutions

NetEngine should create default in-world institutions because an autonomous internet needs institutional actors even when a user starts with a minimal spec. These defaults give every world a coherent source of truth and a place to attach future governance policy.

### Root naming authority

The root naming authority owns the world's root naming hierarchy and delegates top-level or platform zones. It answers the question "who is authoritative for this name tree?" without relying on the public DNS root.

### Numbering authority

The numbering authority allocates internal address ranges, service ranges, and future autonomous-system-like identifiers. It prevents conflicting assignments and provides an audit trail for who received which numbers.

### Default registrar

The default registrar is the operator-facing institution that accepts domain registration intents and applies eligibility policy. It separates the act of requesting or managing a name from the lower-level domain registry that stores authoritative records.

### Domain registry

The domain registry is the authoritative database of registered domains, delegations, contacts, and WHOIS-like metadata. It is the durable truth source for domain ownership and delegation inside the world.

### Trust authority

The trust authority owns world trust roots, certificate issuance policy, ACME behavior, revocation, and future cross-world trust imports. It answers "which cryptographic assertions does this world accept?"

### Platform identity authority

The platform identity authority authenticates operators and platform services that administer the world. It is distinct from in-world user identity so control-plane access can be governed independently from tenant or organization membership.

### In-world identity authority

The in-world identity authority manages identities that exist inside the world: organization users, service accounts, application clients, and realm-to-realm relationships. It lets applications use world-native identity without inheriting platform administrator authority.

### Transit authority

The transit authority governs how reachability is provided between administrative network domains, world services, peers, federations, and the public internet. It owns routing intent, gateway posture, and future transit contracts.

### Mail authority

The mail authority governs world mail domains, routing, mailbox/service identity, and future anti-abuse policy. Mail is both an application service and an authority-bearing namespace, so it should not be treated as a generic container.

### Service catalog authority

The service catalog authority controls which services are discoverable, by whom, and under which names or capabilities. It gives the world a governed discovery plane instead of relying on ad hoc URLs, container names, or external directories.

## Consequences

- Specifications should continue to describe institutions and policy intent, not only implementation resources.
- Gateway, federation, and peering features should keep authority semantics separate from packet-level connectivity.
- MVP implementations may be lightweight, but must avoid baking in assumptions that make later persistence, self-hosting, or federation authority records impossible.
- Documentation should describe NetEngine worlds as authority-autonomous runtimes so operators understand why default institutions exist.

## Open questions

- What is the minimal serialized authority model needed before persistent worlds are supported?
- Which authorities need explicit import/export records in the first federation milestone?
- Should default institutions be user-visible spec objects, implicit generated objects, or both?
- How should authority conflicts be represented when two worlds federate overlapping namespaces?

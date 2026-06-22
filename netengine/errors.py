"""NetEngine exception hierarchy."""


class NetEngineError(Exception):
    """Base class for all NetEngine exceptions."""


class SubstrateError(NetEngineError):
    """Phase 0: Docker/substrate provisioning failure."""


class DNSError(NetEngineError):
    """Phase 1-2: DNS zone or record failure."""


class PKIError(NetEngineError):
    """Phase 3: PKI / step-ca failure."""


class IdentityError(NetEngineError):
    """Phase 4 / 6: Identity provider failure."""


class RegistryError(NetEngineError):
    """Phase 5: World or domain registry failure."""


class GatewayError(NetEngineError):
    """Phase 7: Gateway / nftables rule failure."""


class ServicesError(NetEngineError):
    """Phase 8: World services failure."""

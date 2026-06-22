from typing import Any


class BaseNetEngineException(Exception):
    def __init__(
        self,
        message: str = "An unknown NetEngine exception occurred.",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._msg = message
        self._code: int | str | None = None
        self._log_rules: dict[str, Any] = {
            "log_on_init": True,
            "at_lvl": "TRACE",
            "with_msg": message,
        }
        self._log_xt: dict[str, Any] = dict(kwargs)
        super().__init__(self.message)

    @property
    def message(self) -> str:
        return self._msg or "An unknown NetEngine exception occurred."


class SubstrateError(BaseNetEngineException):
    """Phase 0: container orchestrator / network setup failure."""


class DNSError(BaseNetEngineException):
    """Phases 1-2: DNS zone or record operation failure."""


class PKIError(BaseNetEngineException):
    """Phase 3: certificate authority or cert issuance failure."""


class IdentityError(BaseNetEngineException):
    """Phase 4/6: Keycloak realm or user management failure."""


class RegistryError(BaseNetEngineException):
    """Phase 5: world or domain registry operation failure."""


class GatewayError(BaseNetEngineException):
    """Phase 7: nftables / gateway rule failure."""


class ServicesError(BaseNetEngineException):
    """Phase 8: world services (mail, storage, apps) failure."""

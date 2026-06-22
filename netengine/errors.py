"""NetEngine domain exceptions with optional structured auto-logging on raise.

Each subclass maps to one phase or subsystem; callers pass keyword context
which is forwarded to the loguru sink as bound fields.

Usage::

    raise DNSError("Zone not found", zone="platform.internal")
    raise PKIError("CA bootstrap failed", cause=exc)
"""

from typing import Any, Optional


class BaseNetEngineException(Exception):
    """Base for all NetEngine exceptions.

    Subclasses should set ``code`` and ``default_message`` as class attributes.
    On instantiation the exception optionally logs itself via the loguru
    framework (``log_on_init = True``); any logging failure is swallowed so
    it never masks the real error.
    """

    code: str = "NETENGINE"
    default_message: str = "An unknown NetEngine error occurred."
    log_on_init: bool = True
    log_level: str = "ERROR"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        cause: Optional[BaseException] = None,
        **context: Any,
    ) -> None:
        self._message = message or self.default_message
        self._context: dict[str, Any] = context

        super().__init__(str(self))

        if cause is not None:
            self.__cause__ = cause

        if self.log_on_init:
            self._log()

    def _log(self) -> None:
        try:
            from netengine.logging import get_logger  # lazy — avoids import cycles

            log = get_logger("netengine.errors").bind(error_code=self.code, **self._context)
            log.log(self.log_level, self._message)
        except Exception:
            pass

    @property
    def context(self) -> dict[str, Any]:
        return dict(self._context)

    def __str__(self) -> str:
        return f"[{self.code}] {self._message}"


class SubstrateError(BaseNetEngineException):
    """Phase 0: container orchestrator or network setup failure."""

    code = "SUBSTRATE"
    default_message = "Substrate initialisation failed."


class DNSError(BaseNetEngineException):
    """Phases 1-2: DNS zone or record operation failure."""

    code = "DNS"
    default_message = "DNS operation failed."


class PKIError(BaseNetEngineException):
    """Phase 3: certificate authority or cert issuance failure."""

    code = "PKI"
    default_message = "PKI operation failed."


class IdentityError(BaseNetEngineException):
    """Phase 4/6: Keycloak realm or user management failure."""

    code = "IDENTITY"
    default_message = "Identity operation failed."


class RegistryError(BaseNetEngineException):
    """Phase 5: world or domain registry operation failure."""

    code = "REGISTRY"
    default_message = "Registry operation failed."


class GatewayError(BaseNetEngineException):
    """Phase 7: nftables / gateway rule failure."""

    code = "GATEWAY"
    default_message = "Gateway rule operation failed."


class ServicesError(BaseNetEngineException):
    """Phase 8: world services (mail, storage, apps) failure."""

    code = "SERVICES"
    default_message = "World services operation failed."

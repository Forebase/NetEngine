# TODO: NetEngine exceptions, error handling/reporting/etc module

from typing import Any

from omegaconf import DictConfig as Config

from .logging import get_logger


class BaseNetEngineException(Exception):  # TODO: Comprehensive Engine Exception Base
    def __init__(
        self,
        message: str = "An unknown NetEngine exception occurred.",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._msg = message
        self._code: int | str | None = None
        self._log_rules: Config | dict[str, Any] = Config(
            {
                "log_on_init": True,
                "at_lvl": "TRACE",
                "with_msg": self.message,
            }
        )
        self._log_xt: Config | dict[str, Any] = Config({})

        if kwargs:
            for k, v in kwargs.items():
                self._log_xt.update({k: v})

        super().__init__(self.message)

    @property
    def message(self) -> str:
        return self._msg or "An unknown NetEngine exception occurred."

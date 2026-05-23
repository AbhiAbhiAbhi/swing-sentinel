"""Base class for all trading agents."""

import logging
from abc import ABC, abstractmethod
from typing import Any


class BaseAgent(ABC):
    """Every agent must implement ``execute``."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.logger = logging.getLogger(f"agent.{name}")

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        ...

    def log(self, msg: str) -> None:
        self.logger.info("[%s] %s", self.name, msg)

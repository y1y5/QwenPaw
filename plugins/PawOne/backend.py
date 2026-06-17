# -*- coding: utf-8 -*-
"""PawOne plugin entry point."""

from __future__ import annotations

import logging

from qwenpaw.plugins.api import PluginApi

from .handlers import PawOneCommandHandler

logger = logging.getLogger(__name__)


class PawOnePlugin:
    """Register the PawOne cross-session control command."""

    def register(self, api: PluginApi) -> None:
        api.register_control_command(
            PawOneCommandHandler(),
            priority_level=10,
        )
        logger.info("PawOne plugin registered")


plugin = PawOnePlugin()


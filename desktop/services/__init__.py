"""Desktop services — GUI business logic for config, dashboard, bots, project, and history."""

from desktop.services.config_service import ConfigService
from desktop.services.dashboard_service import DashboardService
from desktop.services.history_service import HistoryService

__all__ = [
    "ConfigService",
    "DashboardService",
    "HistoryService",
]

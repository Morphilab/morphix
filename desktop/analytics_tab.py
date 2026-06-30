"""Analytics Tab — métricas del sistema en tiempo real."""

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

from desktop.async_helpers import run_async
from desktop.theme import ACCENT, StyleFactory


class AnalyticsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._start_refresh()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(20, 16, 20, 16)
        main.setSpacing(12)

        title = QLabel("Analytics Dashboard")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {ACCENT};")
        main.addWidget(title)

        # General metrics
        self.metrics_group = QGroupBox("Métricas en Tiempo Real")
        self.metrics_group.setStyleSheet(StyleFactory.group_box())
        self.metrics_form = QFormLayout(self.metrics_group)
        self.metrics_form.setSpacing(6)

        self.metric_labels = {}
        keys = [
            "uptime",
            "total_tokens",
            "workflows",
            "success_rate",
            "llm_calls",
            "tool_calls",
            "rate_limited",
        ]
        for key in keys:
            label = QLabel("—")
            label.setStyleSheet("color: #E5E5E5; font-size: 13px; font-weight: bold;")
            self.metric_labels[key] = label
            self.metrics_form.addRow(QLabel(key.replace("_", " ").capitalize()), label)

        main.addWidget(self.metrics_group)

        # Rate limiter status
        self.rate_group = QGroupBox("Rate Limiter")
        self.rate_group.setStyleSheet(self.metrics_group.styleSheet())
        self.rate_form = QFormLayout(self.rate_group)

        self.rate_labels = {}
        for key in ["minute_used", "minute_max", "hour_used", "hour_max"]:
            label = QLabel("—")
            label.setStyleSheet("color: #E5E5E5; font-size: 13px; font-weight: bold;")
            self.rate_labels[key] = label
            self.rate_form.addRow(QLabel(key.replace("_", " ").capitalize()), label)

        main.addWidget(self.rate_group)
        main.addStretch()

    def _start_refresh(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: run_async(self._refresh()))
        self._timer.start(5000)
        run_async(self._refresh())

    async def _refresh(self):
        try:
            from core.metrics import metrics as m
            from core.rate_limiter import get_rate_limiter

            data = m.to_dict()
            self.metric_labels["uptime"].setText(f"{data['uptime_seconds']}s")
            self.metric_labels["total_tokens"].setText(str(data["total_tokens"]))
            self.metric_labels["workflows"].setText(
                f"{data['completed_workflows']}/{data['total_workflows']} ({data['success_rate']})"
            )
            self.metric_labels["success_rate"].setText(data["success_rate"])
            self.metric_labels["llm_calls"].setText(str(data["llm_calls"]))
            self.metric_labels["tool_calls"].setText(str(data["tool_calls"]))
            self.metric_labels["rate_limited"].setText(str(data["rate_limited"]))

            rl = get_rate_limiter()
            self.rate_labels["minute_used"].setText(str(rl.current_minute_count))
            self.rate_labels["minute_max"].setText(str(rl.max_per_minute))
            self.rate_labels["hour_used"].setText(str(rl.current_hour_count))
            self.rate_labels["hour_max"].setText(str(rl.max_per_hour))
        except Exception as e:
            logger.debug(f"Error en analytics refresh: {e}")

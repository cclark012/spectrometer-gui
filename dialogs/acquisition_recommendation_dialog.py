from __future__ import annotations

import math
from enum import Enum, auto

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.snr_records import AcquisitionSuggestion


class RecommendationChoice(Enum):
    CANCEL = auto()
    APPLY = auto()
    APPLY_AND_ACQUIRE = auto()


class AcquisitionRecommendationDialog(QDialog):
    def __init__(
        self,
        *,
        current_integration_ms: int,
        current_averages: int,
        suggestion: AcquisitionSuggestion,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self.choice = RecommendationChoice.CANCEL

        self.setWindowTitle(
            "Acquisition Recommendation"
        )
        self.resize(420, 260)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        form.addRow(
            "Current integration",
            QLabel(f"{current_integration_ms} ms"),
        )
        form.addRow(
            "Current averages",
            QLabel(str(current_averages)),
        )
        form.addRow(
            "Suggested integration",
            QLabel(
                f"{suggestion.integration_ms} ms"
            ),
        )
        form.addRow(
            "Suggested averages",
            QLabel(str(suggestion.averages)),
        )

        predicted_snr = (
            f"{suggestion.predicted_snr:.3g}"
            if math.isfinite(
                suggestion.predicted_snr
            )
            else "--"
        )

        predicted_fraction = (
            f"{100.0 * suggestion.predicted_peak_fraction:.1f}%"
            if math.isfinite(
                suggestion.predicted_peak_fraction
            )
            else "--"
        )

        form.addRow(
            "Predicted SNR",
            QLabel(predicted_snr),
        )
        form.addRow(
            "Predicted peak fraction",
            QLabel(predicted_fraction),
        )
        form.addRow(
            "Reason / limit",
            QLabel(suggestion.limiting_reason),
        )

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
        )

        apply_button = QPushButton("Apply")
        apply_acquire_button = QPushButton(
            "Apply && Acquire"
        )

        buttons.addButton(
            apply_button,
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        buttons.addButton(
            apply_acquire_button,
            QDialogButtonBox.ButtonRole.AcceptRole,
        )

        apply_button.clicked.connect(
            self._apply
        )
        apply_acquire_button.clicked.connect(
            self._apply_and_acquire
        )
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def _apply(self) -> None:
        self.choice = RecommendationChoice.APPLY
        self.accept()

    def _apply_and_acquire(self) -> None:
        self.choice = (
            RecommendationChoice
            .APPLY_AND_ACQUIRE
        )
        self.accept()

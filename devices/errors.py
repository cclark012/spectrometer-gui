from __future__ import annotations


class SpectrometerCommunicationError(RuntimeError):
    """The active spectrometer transport or device stopped responding."""


class SpectrometerCommandError(RuntimeError):
    """A command failed, but a follow-up health query verified the device."""

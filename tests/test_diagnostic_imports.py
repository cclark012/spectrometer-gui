from __future__ import annotations

import importlib


def test_optional_hardware_diagnostics_are_import_safe() -> None:
    for module_name in (
        "troubleshooting.list_ports_verbose",
        "troubleshooting.newport_dotnet_smoketest",
        "troubleshooting.newport_keyed_smoketest",
        "troubleshooting.obis_probe",
        "troubleshooting.test_newport_dll",
    ):
        module = importlib.import_module(module_name)
        assert callable(module.main)

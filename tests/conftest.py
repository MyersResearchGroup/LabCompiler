import tempfile

import pytest


def pytest_configure(config):
    """Isolate robot configuration and compilation output before tests run."""
    directory = tempfile.TemporaryDirectory(prefix="lab-compiler-opentrons-")
    compilations = tempfile.TemporaryDirectory(prefix="lab-compiler-output-")
    config.add_cleanup(directory.cleanup)
    config.add_cleanup(compilations.cleanup)
    environment = pytest.MonkeyPatch()
    config.add_cleanup(environment.undo)
    environment.setenv("OT_API_CONFIG_DIR", directory.name)
    environment.setenv("LAB_HOME", compilations.name)

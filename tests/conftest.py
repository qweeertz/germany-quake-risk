"""Shared fixtures. The tests need the processed data: run `quakerisk download` and `quakerisk build` first."""
import pytest

from quakerisk.paths import PROCESSED, RAW


def pytest_collection_modifyitems(config, items):
    missing = not (PROCESSED / "engine_functions.json").exists() or not (RAW / "gem").exists()
    if missing:
        skip = pytest.mark.skip(reason="data not built: run `quakerisk download` and `quakerisk build`")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def engine():
    from quakerisk.engine import QuakeEngine
    return QuakeEngine()


@pytest.fixture(scope="session")
def dueren(engine):
    """M6.0 normal-faulting scenario near Düren."""
    return engine.run(6.5, 50.8, 6.0, depth=10, strike=135)

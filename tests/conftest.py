import pytest


@pytest.fixture(autouse=True)
def _enable_db_for_all_tests(db):
    """Every test may touch the database; keeps individual tests free of markers."""

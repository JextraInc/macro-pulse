from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    """Reset structlog between tests.

    test_logging.configure_logging caches sys.stdout in a PrintLoggerFactory
    with cache_logger_on_first_use=True. Under pytest capture that handle is
    closed between tests, so any downstream logger call explodes. Resetting
    defaults between tests keeps the cache from leaking across the suite.
    """
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()

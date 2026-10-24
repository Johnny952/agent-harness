import logging

import pytest


@pytest.fixture(autouse=True)
def _restore_the_waitress_logger():
    """Undo what a service's `run`/`serve` does to the `waitress` logger (ADR 420, 423).

    Each entry point attaches a stderr handler to it and stops it propagating to
    root. Left in place, a later test would inherit a handler bound to a capture
    stream that is already closed.
    """
    logger = logging.getLogger("waitress")
    level, handlers, propagate = logger.level, list(logger.handlers), logger.propagate
    yield
    logger.handlers[:] = handlers
    logger.propagate = propagate
    logger.setLevel(level)  # setLevel also clears the logger-level cache

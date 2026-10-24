import pytest

import dispatcher.dispatcher as dispatcher_mod


@pytest.fixture(autouse=True)
def _no_container_probe(monkeypatch):
    """The account reaper's in-container probe (ADR 429) would run `docker`;
    answer "could not tell" so no test does. A test that wants an answer
    patches `_task_process_seen` again."""
    monkeypatch.setattr(dispatcher_mod, "_task_process_seen", lambda cfg, account, task: None)

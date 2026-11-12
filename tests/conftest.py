"""Shared test setup."""
import functools
import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _history_in_a_temp_dir(tmp_path_factory):
    """The history DB of the test run is its own. The suite used to write its
    records (hello.txt, multi_1.txt, proof_doc.txt, ...) into the developer's
    real one — which is capped at 1000 records, so every run pushed the oldest
    real records out for good."""
    from core import backend
    base = str(tmp_path_factory.mktemp("history"))
    real = backend.history_dir

    def history_dir(session_id=None):
        d = os.path.join(base, session_id) if session_id else base
        os.makedirs(d, exist_ok=True)
        return d
    backend.history_dir = history_dir
    # ... and with the history elsewhere, a sweep sees nothing that "can still be
    # continued": it must never run on the developer's real folders (every task
    # that ends starts one). Only where a test set the folders itself.
    from core import retention
    real_run = retention.run_retention
    tmp_root = os.path.normcase(os.path.realpath(str(tmp_path_factory.getbasetemp())))

    @functools.wraps(real_run)                  # tests read its source (inspect follows __wrapped__)
    def run_retention(*a, **k):
        try:
            from core import paths
            temp_dir, result_dir, _ = backend.get_custom_paths()
            inside = all(os.path.normcase(os.path.realpath(p)).startswith(tmp_root)
                         for p in (temp_dir, result_dir, paths.DATA_DIR))
        except Exception:  # noqa: BLE001
            inside = False
        if inside:
            return real_run(*a, **k)
    retention.run_retention = run_retention
    yield
    backend.history_dir = real
    retention.run_retention = real_run

"""An uninstall blocked by loaded native libs is queued, and the next start
(run_pending_uninstalls) finishes it and clears the queue."""
import core.paths
import core.optional_modules as om
from core import module_manager as mm


def test_blocked_uninstall_is_queued_then_run_at_start(tmp_path, monkeypatch):
    monkeypatch.setattr(core.paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mm.plugins_registry, "get", lambda name: {"name": name})
    monkeypatch.setattr(mm, "packages_to_uninstall", lambda name: ["numpy"])
    monkeypatch.setattr(mm, "_transitive_to_remove", lambda name: [])
    monkeypatch.setattr(mm, "_drop_still_required", lambda pkgs: pkgs)
    monkeypatch.setattr(mm, "_loaded_blockers", lambda pkgs: {"numpy"})

    ok, out = mm.uninstall_module("PDF")
    assert (ok, out) == (False, mm.PENDING_RESTART)
    assert mm.pending_uninstalls() == ["PDF"]
    mm.uninstall_module("PDF")                       # queued once, not twice
    assert mm.pending_uninstalls() == ["PDF"]

    ran = []
    monkeypatch.setattr(om, "uninstall_plugin", lambda name: ran.append(name) or (True, "ok", 0))
    mm.run_pending_uninstalls()
    assert ran == ["PDF"] and mm.pending_uninstalls() == []


def test_pending_does_not_delete_models_now(monkeypatch):
    monkeypatch.setattr(mm, "uninstall_module", lambda name: (False, mm.PENDING_RESTART))
    called = []
    monkeypatch.setattr(om, "cleanup_plugin_models", lambda name: called.append(name) or ([], 0))
    assert om.uninstall_plugin("PDF") == (False, mm.PENDING_RESTART, 0)
    assert called == []                              # models go with the real uninstall

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


def test_locked_install_is_queued_once_then_taken(tmp_path, monkeypatch):
    monkeypatch.setattr(core.paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mm, "_RETRIED_AFTER_RESTART", set())
    monkeypatch.setattr(mm.plugins_registry, "requirements_path", lambda name: "req.txt")
    monkeypatch.setattr(mm.plugins_registry, "get", lambda name: {"name": name, "key": "pdf"})
    monkeypatch.setattr(mm, "_frozen_block", lambda: None)
    monkeypatch.setattr(mm, "_freeze_names", lambda: set())
    monkeypatch.setattr(mm, "_maybe_install_cuda_torch", lambda name: None)
    monkeypatch.setattr(mm, "_run_install", lambda req, upgrade=False: (False, "os error 5" + mm._LOCK_MARK))

    assert mm.install_module("PDF") == (False, mm.PENDING_INSTALL_RESTART)
    assert mm.pending_installs() == ["PDF"]
    assert mm.take_pending_installs() == ["PDF"] and mm.pending_installs() == []
    # Still locked after the restart: report it, don't queue again (no restart loop).
    ok, out = mm.install_module("PDF")
    assert not ok and out != mm.PENDING_INSTALL_RESTART and mm.pending_installs() == []

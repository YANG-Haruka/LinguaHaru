# Tests for the env-wide plugin version caps (plugins/constraints.txt +
# core/module_manager._global_constraint_args). Background: a numpy 2.5.x pulled
# as "latest" by one plugin made the Video/Audio install permanently
# unresolvable (no stable numba supports numpy>=2.5, and installs pin the
# already-installed set), so every install command must pass the shared caps.
#
# No network is used.
#
# Run from the repo root:
#   python tests/test_plugin_constraints.py
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from core import module_manager as mm

PASS = 0


def ok(name):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}")


def _spec_lines(path):
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f
                if ln.strip() and not ln.strip().startswith("#")]


def test_constraints_file_valid():
    path = os.path.join(REPO_ROOT, "plugins", "constraints.txt")
    assert os.path.exists(path), "plugins/constraints.txt must ship with the app"
    assert mm._GLOBAL_CONSTRAINTS == path
    lines = _spec_lines(path)
    assert lines, "constraints file must not be empty"
    # pip/uv constraint files accept ONLY plain version specs — no -r includes,
    # no editables, no bare names (a cap without an operator does nothing).
    spec = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\s*(==|!=|<=|>=|<|>|~=)")
    for ln in lines:
        assert spec.match(ln), f"invalid constraint line: {ln!r}"
    ok("constraints.txt exists and every line is a valid version spec")


def test_constraints_cap_numpy_below_25():
    lines = _spec_lines(mm._GLOBAL_CONSTRAINTS)
    caps = [ln for ln in lines if re.match(r"^numpy\s*[<>=!~]", ln)]
    assert caps, "constraints.txt must cap numpy"
    assert any("<2.5" in ln.replace(" ", "") for ln in caps), caps
    ok("constraints.txt caps numpy below 2.5 (stable numba ceiling)")


def test_global_constraint_args():
    assert mm._global_constraint_args() == ["--constraint", mm._GLOBAL_CONSTRAINTS]
    real = mm._GLOBAL_CONSTRAINTS
    try:
        mm._GLOBAL_CONSTRAINTS = os.path.join(REPO_ROOT, "plugins", "no_such_file.txt")
        assert mm._global_constraint_args() == []
    finally:
        mm._GLOBAL_CONSTRAINTS = real
    ok("_global_constraint_args: present -> --constraint, missing -> no-op")


def test_install_cmd_carries_both_constraint_files():
    real_uv = mm._uv_exe
    try:
        for uv in (None, "uv.exe"):   # pip fallback AND uv front-end
            mm._uv_exe = lambda _uv=uv: _uv
            cmd = mm._install_cmd("req.txt", "https://pypi.org/simple", upgrade=False)
            assert mm._GLOBAL_CONSTRAINTS in cmd, cmd
            # A per-run freeze file must ADD to (not replace) the global caps.
            cmd = mm._install_cmd("req.txt", "https://pypi.org/simple", upgrade=False,
                                  constraints="freeze.txt")
            assert cmd.count("--constraint") == 2 and "freeze.txt" in cmd, cmd
    finally:
        mm._uv_exe = real_uv
    ok("_install_cmd passes global caps alongside the freeze constraints")


def test_every_install_site_is_constrained():
    # The CUDA torch/onnxruntime swaps, opencv normalization and upgrade path
    # build their pip/uv commands inline (closures) — a source-level tripwire is
    # the practical way to keep them all on the shared caps.
    with open(os.path.join(REPO_ROOT, "core", "module_manager.py"), encoding="utf-8") as f:
        src = f.read()
    calls = src.count("_global_constraint_args(") - 1   # minus the def itself
    assert calls >= 7, (
        f"only {calls} install sites pass _global_constraint_args() — a pip/uv "
        "install command in module_manager lost the env-wide caps")
    ok("all module_manager install sites pass the global constraint file")


def test_plugin_requirements_have_no_uncapped_numpy():
    # A bare 'numpy' requirement is exactly how a too-new numpy poisoned the env.
    plugdir = os.path.join(REPO_ROOT, "plugins")
    seen = 0
    for key in sorted(os.listdir(plugdir)):
        req = os.path.join(plugdir, key, "requirements.txt")
        if not os.path.isfile(req):
            continue
        for ln in _spec_lines(req):
            if re.match(r"^numpy(\W|$)", ln):
                seen += 1
                assert "<2.5" in ln.replace(" ", ""), f"{key}: uncapped numpy: {ln!r}"
    assert seen >= 2, "expected numpy caps in at least ocr + video requirements"
    ok("no plugin requirements file carries an uncapped numpy")


if __name__ == "__main__":
    test_constraints_file_valid()
    test_constraints_cap_numpy_below_25()
    test_global_constraint_args()
    test_install_cmd_carries_both_constraint_files()
    test_every_install_site_is_constrained()
    test_plugin_requirements_have_no_uncapped_numpy()
    print(f"\nAll {PASS} checks passed.")

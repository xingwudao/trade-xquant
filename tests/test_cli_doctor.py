from __future__ import annotations

import os
import subprocess
import sys


def test_doctor_does_not_require_pyyaml_to_import() -> None:
    code = """
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'yaml':
        raise ModuleNotFoundError("No module named 'yaml'")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from trade_xquant.cli import main
raise SystemExit(main(['doctor', '--config', 'missing.yaml']))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.getcwd()

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert "No module named 'yaml'" not in result.stderr
    assert '"config_exists": false' in result.stdout

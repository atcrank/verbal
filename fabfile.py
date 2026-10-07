"""
Fabric entrypoint for Verbal / Reason.
Exposes modular deployment tasks defined in nginx/fabric_deploy.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "nginx"))
from fabric_deploy import *  # noqa: F401, F403

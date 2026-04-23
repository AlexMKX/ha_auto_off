"""
Pytest fixtures - conditionally loads unit or e2e fixtures based on environment.

ha-test-kit sets AUTOQA_MODE=unit or AUTOQA_MODE=e2e.
"""

import os
import sys

_current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _current_dir)

_mode = os.environ.get("AUTOQA_MODE", "").strip().lower()

if _mode == "e2e":
    from conftest_e2e import *  # noqa: F401,F403
elif _mode == "unit":
    from conftest_unit import *  # noqa: F401,F403
else:
    raise RuntimeError(
        "AUTOQA_MODE must be set to 'unit' or 'e2e'. "
        "Run via ha-test-kit (./ha-test-kit/run_unit.sh or ./ha-test-kit/run_e2e.sh), "
        "or set AUTOQA_MODE explicitly."
    )

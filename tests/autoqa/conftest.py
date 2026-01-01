"""
Pytest fixtures - conditionally loads unit or e2e fixtures based on environment.
"""
import os
import sys

# Add current directory and /tests to path
_current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _current_dir)
sys.path.insert(0, '/tests')
sys.path.insert(0, '/app')

# Check if we're running in docker (no homeassistant module available)
try:
    from homeassistant.core import HomeAssistant
    # Running locally with HA installed - load unit fixtures
    from conftest_unit import *
except ImportError:
    # Running in docker container - load e2e fixtures
    from conftest_e2e import *

#!/bin/bash
# E2E test runner script
# Usage: ./tests/e2e/run_e2e_tests.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Auto Off E2E Tests ==="
echo "Project directory: $PROJECT_DIR"

# Check if hass is available
if ! command -v hass &> /dev/null; then
    echo "Error: 'hass' command not found. Please install Home Assistant."
    echo "You can install it with: pip install homeassistant"
    exit 1
fi

# Check if port 18123 is available
if lsof -Pi :18123 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: Port 18123 is already in use. Please stop any running HA instance."
    exit 1
fi

# Install test dependencies
echo "Installing test dependencies..."
pip install pytest pytest-asyncio aiohttp --quiet

# Run e2e tests
echo "Running E2E tests..."
cd "$PROJECT_DIR"
python -m pytest tests/e2e/ -v -s --tb=short "$@"

echo "=== E2E Tests Complete ==="

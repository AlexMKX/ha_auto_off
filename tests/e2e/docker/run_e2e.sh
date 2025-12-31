#!/bin/bash
# E2E Test Runner Script
# Starts Docker Compose stack and runs tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Auto Off E2E Test Runner ===${NC}"

# Cleanup function
cleanup() {
    echo -e "\n${YELLOW}Cleaning up...${NC}"
    docker compose down -v --remove-orphans 2>/dev/null || true
}

# Set trap for cleanup on exit
trap cleanup EXIT

# Parse arguments
KEEP_RUNNING=false
REBUILD=false
TEST_FILTER=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --keep|-k)
            KEEP_RUNNING=true
            shift
            ;;
        --rebuild|-r)
            REBUILD=true
            shift
            ;;
        --filter|-f)
            TEST_FILTER="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --keep, -k     Keep containers running after tests"
            echo "  --rebuild, -r  Force rebuild of Docker images"
            echo "  --filter, -f   Filter tests by keyword (e.g., 'test_login')"
            echo "  --help, -h     Show this help"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Clean up old containers
echo -e "${YELLOW}Stopping any existing containers...${NC}"
docker compose down -v --remove-orphans 2>/dev/null || true

# Create necessary directories
mkdir -p test_results screenshots ha_config

# Build options
BUILD_OPTS=""
if [ "$REBUILD" = true ]; then
    BUILD_OPTS="--build --no-cache"
else
    BUILD_OPTS="--build"
fi

# Start Home Assistant first
echo -e "${YELLOW}Starting Home Assistant...${NC}"
docker compose up -d homeassistant $BUILD_OPTS

# Wait for HA to be healthy
echo -e "${YELLOW}Waiting for Home Assistant to be ready...${NC}"
MAX_WAIT=300
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if docker compose exec -T homeassistant curl -s http://localhost:8123/api/ > /dev/null 2>&1; then
        echo -e "${GREEN}Home Assistant is ready!${NC}"
        break
    fi
    sleep 5
    WAITED=$((WAITED + 5))
    echo "  Waited ${WAITED}s..."
done

if [ $WAITED -ge $MAX_WAIT ]; then
    echo -e "${RED}Home Assistant did not start within ${MAX_WAIT}s${NC}"
    docker compose logs homeassistant
    exit 1
fi

# Build test command
TEST_CMD="python -m pytest /tests/test_e2e_playwright.py -v --html=/test_results/report.html --self-contained-html"
if [ -n "$TEST_FILTER" ]; then
    TEST_CMD="$TEST_CMD -k '$TEST_FILTER'"
fi

# Run tests
echo -e "${YELLOW}Running E2E tests...${NC}"
docker compose run --rm autoqa bash -c "$TEST_CMD"
TEST_EXIT_CODE=$?

# Show results
echo ""
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}=== All tests passed! ===${NC}"
else
    echo -e "${RED}=== Some tests failed (exit code: $TEST_EXIT_CODE) ===${NC}"
fi

# Show test report location
echo -e "${YELLOW}Test report: ${SCRIPT_DIR}/test_results/report.html${NC}"
echo -e "${YELLOW}Screenshots: ${SCRIPT_DIR}/screenshots/${NC}"

# Keep running if requested
if [ "$KEEP_RUNNING" = true ]; then
    echo -e "${YELLOW}Containers are still running. Press Ctrl+C to stop.${NC}"
    echo -e "Home Assistant: http://localhost:8123"
    trap - EXIT  # Remove cleanup trap
    wait
fi

exit $TEST_EXIT_CODE

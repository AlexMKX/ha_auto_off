# Auto Off E2E Tests

End-to-end tests for the Auto Off Home Assistant integration using Docker, pytest, and Playwright.

## Quick Start

```bash
# Run all tests
./run_e2e.sh

# Run tests and keep containers running for debugging
./run_e2e.sh --keep

# Rebuild Docker images and run tests
./run_e2e.sh --rebuild

# Run specific tests
./run_e2e.sh --filter "test_login"
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Compose                        │
├─────────────────────────┬───────────────────────────────┤
│   homeassistant:8123    │         autoqa                │
│   - Home Assistant      │   - Python 3.11               │
│   - auto_off component  │   - pytest                    │
│   - test entities       │   - playwright                │
└─────────────────────────┴───────────────────────────────┘
```

## Test Credentials

- **Username:** `test_admin`
- **Password:** `test_password_123`
- **URL:** `http://localhost:8123`

## Test Entities

| Entity | Type | Description |
|--------|------|-------------|
| `binary_sensor.test_motion` | Motion | Main motion sensor |
| `binary_sensor.test_motion_2` | Motion | Secondary motion sensor |
| `binary_sensor.test_door` | Door | Door sensor |
| `light.test_light` | Light | Test light 1 |
| `light.test_light_2` | Light | Test light 2 |
| `switch.test_switch` | Switch | Test switch |

## Files

```
docker/
├── docker-compose.yml      # Docker Compose config
├── Dockerfile.autoqa       # Test container Dockerfile
├── requirements.txt        # Python dependencies
├── provisioning.py         # HA provisioning utilities
├── conftest_docker.py      # Pytest fixtures
├── pytest.ini              # Pytest config
├── run_e2e.sh              # Test runner script
├── ha_config/
│   └── configuration.yaml  # HA test configuration
├── test_results/           # HTML test reports
└── screenshots/            # Playwright screenshots
```

## Test Results

After running tests:
- **HTML Report:** `test_results/report.html`
- **Screenshots:** `screenshots/`

## Manual Testing

```bash
# Start stack and keep running
./run_e2e.sh --keep

# Open Home Assistant in browser
xdg-open http://localhost:8123

# Run tests manually in container
docker compose exec autoqa python -m pytest /tests/test_e2e_playwright.py -v

# View logs
docker compose logs -f homeassistant
```

## Troubleshooting

### Container won't start
```bash
docker compose down -v
docker compose build --no-cache
docker compose up
```

### Tests fail on first run
Home Assistant may need more time to initialize. Increase timeout or run with `--keep` and debug manually.

### Playwright browser issues
```bash
# Reinstall browsers in container
docker compose exec autoqa playwright install chromium
```

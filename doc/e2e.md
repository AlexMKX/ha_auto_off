# Auto Off E2E Test Cases

## Overview

This document describes end-to-end test cases for the Auto Off Home Assistant integration. Tests run against a real Home Assistant instance in Docker using pytest and Playwright.

## Test Environment

### Architecture
```
┌─────────────────────────────────────────────────────────┐
│                    Docker Compose                        │
├─────────────────────────┬───────────────────────────────┤
│   homeassistant         │         autoqa                │
│   ┌─────────────────┐   │   ┌─────────────────────┐     │
│   │ Home Assistant  │   │   │ pytest + playwright │     │
│   │ + auto_off      │◄──┼───│ + provisioning      │     │
│   │ + test entities │   │   │                     │     │
│   └─────────────────┘   │   └─────────────────────┘     │
│        :8123            │                               │
└─────────────────────────┴───────────────────────────────┘
```

### Test Entities
| Entity ID | Type | Purpose |
|-----------|------|---------|
| `binary_sensor.test_motion` | Binary Sensor | Motion sensor simulation |
| `binary_sensor.test_motion_2` | Binary Sensor | Second motion sensor |
| `binary_sensor.test_door` | Binary Sensor | Door sensor simulation |
| `light.test_light` | Light | Controllable test light |
| `light.test_light_2` | Light | Second test light |
| `switch.test_switch` | Switch | Controllable test switch |

---

## Test Cases

### TC-001: Home Assistant Connectivity
**Category:** Setup  
**Priority:** Critical  

**Description:** Verify Home Assistant is running and accessible.

**Steps:**
1. Start Docker Compose stack
2. Wait for Home Assistant healthcheck
3. Call `/api/` endpoint

**Expected Result:** API returns `{"message": "API running."}`

---

### TC-002: Test Entities Exist
**Category:** Setup  
**Priority:** Critical  

**Description:** Verify all required test entities are created.

**Steps:**
1. Start Home Assistant
2. Get all entity states via API
3. Check for required entities

**Expected Result:** All test entities exist and are in expected initial state.

---

### TC-003: Integration Installation
**Category:** Integration Setup  
**Priority:** Critical  

**Description:** Verify auto_off integration can be installed via config flow.

**Steps:**
1. Call config flow API to start auto_off setup
2. Provide poll_interval configuration
3. Complete config flow

**Expected Result:** Integration is created and appears in config entries.

---

### TC-004: Create Group via Service
**Category:** Service Calls  
**Priority:** High  

**Description:** Verify auto_off groups can be created via `auto_off.set_group` service.

**Steps:**
1. Call `auto_off.set_group` service with:
   - group_name: "test_group"
   - sensors: ["binary_sensor.test_motion"]
   - targets: ["light.test_light"]
   - delay: 5
2. Wait for group creation

**Expected Result:** Group is created without errors.

---

### TC-005: Delete Group via Service
**Category:** Service Calls  
**Priority:** High  

**Description:** Verify auto_off groups can be deleted via `auto_off.delete_group` service.

**Steps:**
1. Create a test group
2. Call `auto_off.delete_group` with group_name
3. Wait for deletion

**Expected Result:** Group is removed without errors.

---

### TC-006: Light Stays On with Motion
**Category:** Core Functionality  
**Priority:** Critical  

**Description:** Light should stay ON while motion sensor is active.

**Steps:**
1. Create auto_off group with motion sensor and light
2. Turn ON motion sensor
3. Turn ON light
4. Wait 5+ seconds

**Expected Result:** Light remains ON because motion is detected.

---

### TC-007: Light Turns Off After Motion Stops
**Category:** Core Functionality  
**Priority:** Critical  

**Description:** Light should turn OFF after motion stops and delay expires.

**Steps:**
1. Create auto_off group with delay=0
2. Turn ON motion sensor
3. Turn ON light
4. Turn OFF motion sensor
5. Wait for poll interval + buffer

**Expected Result:** Light turns OFF automatically.

---

### TC-008: Multiple Sensors
**Category:** Core Functionality  
**Priority:** High  

**Description:** Light should stay ON if ANY sensor is active.

**Steps:**
1. Create group with two motion sensors
2. Turn ON sensor 1 and light
3. Turn OFF sensor 1, turn ON sensor 2
4. Wait for poll interval

**Expected Result:** Light stays ON because sensor 2 is active.

---

### TC-009: Multiple Targets
**Category:** Core Functionality  
**Priority:** High  

**Description:** All targets should turn OFF when sensors become inactive.

**Steps:**
1. Create group with one sensor and two lights
2. Turn ON sensor and both lights
3. Turn OFF sensor
4. Wait for auto-off

**Expected Result:** Both lights turn OFF.

---

### TC-010: Switch Target
**Category:** Core Functionality  
**Priority:** Medium  

**Description:** Switches should also be turned OFF by auto_off.

**Steps:**
1. Create group with motion sensor and switch target
2. Turn ON motion and switch
3. Turn OFF motion
4. Wait for auto-off

**Expected Result:** Switch turns OFF.

---

### TC-011: Orphaned Target Turns Off
**Category:** Core Functionality  
**Priority:** High  

**Description:** Target turned ON without active sensor (e.g., physical switch, zigbee noise) should be turned OFF immediately when delay=0.

**Steps:**
1. Create group with delay=0
2. Verify motion sensor is OFF
3. Turn ON light externally (no motion trigger)
4. Wait for integration to detect

**Expected Result:** Light is turned OFF because no sensor is active.

**Note:** Testing the "expired deadline cleanup" scenario (timer lost after HA restart) requires HA restart and is covered by unit tests.

---

### TC-012: Delay Prevents Immediate Off
**Category:** Delay Functionality  
**Priority:** High  

**Description:** Light should NOT turn OFF immediately when delay is configured.

**Steps:**
1. Create group with delay=1 (1 minute)
2. Turn ON motion and light
3. Turn OFF motion
4. Wait 5 seconds

**Expected Result:** Light is still ON (within delay period).

---

### TC-013: Motion During Delay Cancels Timer
**Category:** Delay Functionality  
**Priority:** High  

**Description:** Motion during delay period should cancel the turn-off timer.

**Steps:**
1. Create group with delay
2. Turn ON motion and light
3. Turn OFF motion (starts timer)
4. Wait 2 seconds
5. Turn ON motion again
6. Wait beyond original timeout

**Expected Result:** Light stays ON because timer was cancelled.

---

### TC-014: UI Login
**Category:** UI Testing  
**Priority:** Medium  

**Description:** Verify login to Home Assistant via UI works.

**Steps:**
1. Navigate to Home Assistant URL
2. Enter test credentials
3. Submit login form

**Expected Result:** Successfully logged in, dashboard visible.

---

### TC-015: Navigate to Settings
**Category:** UI Testing  
**Priority:** Low  

**Description:** Verify navigation to Settings page.

**Steps:**
1. Login to Home Assistant
2. Click Settings in sidebar
3. Wait for page load

**Expected Result:** Settings page is displayed.

---

### TC-016: View Auto Off Integration
**Category:** UI Testing  
**Priority:** Low  

**Description:** Verify Auto Off integration is visible in UI.

**Steps:**
1. Login to Home Assistant
2. Navigate to Settings > Integrations
3. Find Auto Off integration

**Expected Result:** Auto Off integration card is visible.

---

### TC-017: Entity Unavailable Handling
**Category:** Edge Cases  
**Priority:** Medium  

**Description:** Verify graceful handling when target entity is unavailable.

**Steps:**
1. Create group with existing and non-existing targets
2. Turn ON sensor
3. Wait for processing

**Expected Result:** No crash, existing targets work normally.

---

### TC-018: Rapid State Changes
**Category:** Edge Cases  
**Priority:** Medium  

**Description:** Verify handling of rapid sensor state changes.

**Steps:**
1. Create group with motion sensor and light
2. Turn ON light
3. Rapidly toggle motion sensor (5 times, 0.5s interval)
4. End with motion ON
5. Wait for stabilization

**Expected Result:** Light remains ON, no race conditions.

---

### TC-019: Update Group Config
**Category:** Configuration  
**Priority:** Medium  

**Description:** Verify existing group configuration can be updated.

**Steps:**
1. Create group with one sensor, one target
2. Call set_group with same name but more sensors/targets
3. Wait for update

**Expected Result:** Group is updated with new configuration.

---

## Running Tests

### Quick Start
```bash
cd tests/e2e/docker
./run_e2e.sh
```

### Options
```bash
# Keep containers running after tests
./run_e2e.sh --keep

# Force rebuild Docker images
./run_e2e.sh --rebuild

# Run specific test
./run_e2e.sh --filter "test_login"
```

### View Results
- HTML Report: `tests/e2e/docker/test_results/report.html`
- Screenshots: `tests/e2e/docker/screenshots/`

### Manual Testing
```bash
# Start stack and keep running
./run_e2e.sh --keep

# Access Home Assistant
open http://localhost:8123
# Login: test_admin / test_password_123
```

---

## Test Matrix

| Test Case | API | UI | Critical | Delay | Multi |
|-----------|-----|----|---------:|------:|------:|
| TC-001 | ✅ | | ✅ | | |
| TC-002 | ✅ | | ✅ | | |
| TC-003 | ✅ | | ✅ | | |
| TC-004 | ✅ | | | | |
| TC-005 | ✅ | | | | |
| TC-006 | ✅ | | ✅ | | |
| TC-007 | ✅ | | ✅ | | |
| TC-008 | ✅ | | | | ✅ |
| TC-009 | ✅ | | | | ✅ |
| TC-010 | ✅ | | | | |
| TC-011 | ✅ | | ✅ | | |
| TC-012 | ✅ | | | ✅ | |
| TC-013 | ✅ | | | ✅ | |
| TC-014 | | ✅ | | | |
| TC-015 | | ✅ | | | |
| TC-016 | | ✅ | | | |
| TC-017 | ✅ | | | | |
| TC-018 | ✅ | | | | |
| TC-019 | ✅ | | | | |

---

## Future Test Cases

### Planned
- **TC-020:** Template sensor support
- **TC-021:** Template target support (dynamic entity lists)
- **TC-022:** Template delay support
- **TC-023:** Home Assistant restart persistence
- **TC-024:** Concurrent group updates
- **TC-025:** Device registry cleanup on group delete
- **TC-026:** Long-running stability test (24h)

### Integration Tests
- HACS installation flow
- Integration migration (version upgrades)
- Multi-user access control

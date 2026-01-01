# Auto Off Deadline Logic

## Overview

The Auto Off integration automatically turns off target entities (lights, switches, etc.) after a configurable delay when the sensor group becomes inactive (all sensors OFF).

## Sensor Group Logic

A **sensor group** is a list of sensors and/or templates. The group state is determined by **OR logic**:

- **Sensor Group ON**: At least ONE sensor in the group is ON (activity detected)
- **Sensor Group OFF**: ALL sensors in the group are OFF (no activity)

```
Sensor Group State = sensor1 OR sensor2 OR sensor3 OR ...
```

## Key Principles

### Deadline Behavior

The **deadline** is the timestamp when targets will be turned off. It is managed according to these rules:

#### 1. Startup / First Run
- **Condition**: Sensor group OFF, any target ON, no deadline set
- **Action**: Set deadline = now + delay
- **Rationale**: If system starts with targets on and no activity, schedule turn-off

#### 2. Sensor Group Becomes Active (ON)
- **Condition**: Sensor group transitions to ON state (any sensor becomes ON), OR periodic check sees group ON
- **Action**: Cancel/clear deadline immediately
- **Rationale**: Activity detected → targets should stay on indefinitely

#### 3. Sensor Group Becomes Inactive (OFF)
- **Condition**: Sensor group OFF (all sensors OFF), target ON, no deadline exists
- **Action**: Set deadline = now + delay
- **Rationale**: Activity stopped → start countdown to turn off

#### 4. Target Turns ON While Deadline Exists
- **Condition**: Sensor group OFF, deadline already exists, new target turns ON
- **Action**: 
  - Calculate new_deadline = now + delay
  - If new_deadline > current_deadline: update to new_deadline
  - Otherwise: keep current_deadline
- **Rationale**: Extending deadline only if the new target would be on longer; don't shorten existing deadline

### State Diagram

```
                    ┌─────────────────────────────────────┐
                    │                                     │
                    ▼                                     │
    ┌───────────────────────────┐                        │
    │  No Deadline              │                        │
    │  (sensor group ON or      │                        │
    │   targets OFF)            │                        │
    └───────────────────────────┘                        │
           │                                              │
           │ sensor group OFF + target ON                 │
           ▼                                              │
    ┌───────────────────────────┐   sensor group ON      │
    │  Deadline Active          │ ───────────────────────┘
    │  (countdown running)      │
    └───────────────────────────┘
           │
           │ deadline reached
           ▼
    ┌───────────────────────────┐
    │  Turn Off Targets         │
    └───────────────────────────┘
```

### Summary Table

| Sensor Group | Target | Deadline | Event | Action |
|--------------|--------|----------|-------|--------|
| OFF (all sensors OFF) | ON | None | - | Set deadline |
| OFF | ON | Exists | Target turns ON | Extend if new > old |
| ON (any sensor ON) | Any | Any | - | Clear deadline |
| OFF | OFF | Any | - | Clear deadline |

## Configuration

- **delay**: Time in **minutes** before turning off targets (can be a template)
- **sensors**: List of entity_ids or templates that indicate activity
- **targets**: List of entity_ids or templates to turn off

## Delay Units

- Configuration and UI: **minutes**
- Internal calculation: converted to seconds (`delay * 60`)
- Display (sensor entity): shown as "X min"

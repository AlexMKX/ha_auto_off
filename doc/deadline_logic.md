# Auto Off Deadline Logic

## Overview

The Auto Off integration automatically turns off target entities (lights, switches, etc.) after a configurable delay when all sensor entities (motion sensors, door sensors, etc.) become inactive.

## Key Principles

### Deadline Behavior

The **deadline** is the timestamp when targets will be turned off. It is managed according to these rules:

#### 1. Startup / First Run
- **Condition**: Sensors OFF, any target ON, no deadline set
- **Action**: Set deadline = now + delay
- **Rationale**: If system starts with targets on and no activity, schedule turn-off

#### 2. Sensor Becomes Active (ON)
- **Condition**: Any sensor transitions to ON state, OR periodic check sees sensor ON
- **Action**: Cancel/clear deadline immediately
- **Rationale**: Activity detected → targets should stay on indefinitely

#### 3. All Sensors Become Inactive (OFF)
- **Condition**: Sensors OFF, target ON, no deadline exists
- **Action**: Set deadline = now + delay
- **Rationale**: Activity stopped → start countdown to turn off

#### 4. Target Turns ON While Deadline Exists
- **Condition**: Sensors OFF, deadline already exists, new target turns ON
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
    │  (sensors ON or           │                        │
    │   targets OFF)            │                        │
    └───────────────────────────┘                        │
           │                                              │
           │ sensors OFF + target ON                      │
           ▼                                              │
    ┌───────────────────────────┐      sensor ON         │
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

| Sensors | Target | Deadline | Event | Action |
|---------|--------|----------|-------|--------|
| OFF | ON | None | - | Set deadline |
| OFF | ON | Exists | Target turns ON | Extend if new > old |
| ON | Any | Any | - | Clear deadline |
| OFF | OFF | Any | - | Clear deadline |

## Configuration

- **delay**: Time in **minutes** before turning off targets (can be a template)
- **sensors**: List of entity_ids or templates that indicate activity
- **targets**: List of entity_ids or templates to turn off

## Delay Units

- Configuration and UI: **minutes**
- Internal calculation: converted to seconds (`delay * 60`)
- Display (sensor entity): shown as "X min"

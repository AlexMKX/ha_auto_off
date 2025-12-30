Empty yet

## Auto Off Deadline Attribute



When a group is being monitored for auto-off, the integration automatically sets a custom attribute `auto_off_deadline` for each target entity in the group:

- If the target entity is ON and a deadline is set for the group, the attribute `auto_off_deadline` will contain the ISO 8601 timestamp of the scheduled auto-off deadline.
- If the target entity is OFF, the attribute will be set to `None`.
- The attribute is updated in real time as the group state changes.

This allows you to see, for each controlled entity, when it is scheduled to be automatically turned off.

## Configuration Example

```
auto_off:
  poll_interval: 15  # Interval in seconds for periodic polling (applies to both auto_off and door_occupancy)
  door_occupancy:
    entity_templates:
      - "{{ states.binary_sensor | selectattr('attributes.device_class', 'in', ['door', 'gate']) | map(attribute='entity_id') | list }}"
  auto_off:
    sensors:
      - sensors: ["binary_sensor.motion_kitchen"]
        targets: ["switch.kitchen_light"]
        delay: 5
```

- `poll_interval`: (integer, optional, default: 15) — how often (in seconds) to poll for auto_off and door_occupancy updates. Applies globally to the integration.
- `door_occupancy`: (object, optional) — config for door occupancy sensors.
- `auto_off`: (object, required) — config for auto-off groups (see below).

## Development & Releases

### Automatic Release Process

This project uses GitHub Actions for automatic releases:

- **When**: Every push to `master` branch (except version bump commits and GitHub workflow changes)
- **Version Format**: `YYMMDDHH` (e.g., `24122011` for Dec 20, 2024 at 11:00)
- **What Happens**:
  1. Generates new version based on current date/time
  2. Updates `custom_components/auto_off/manifest.json` with new version
  3. Creates a commit with the version update
  4. Creates a Git tag
  5. Generates changelog from commits since last release
  6. Creates GitHub release with changelog and installation instructions

### HACS Integration

The integration is designed to work with HACS (Home Assistant Community Store):

- `hacs.json` file configures HACS integration
- `manifest.json` contains integration metadata
- Automatic releases are compatible with HACS update notifications

### Code Validation

Pull requests and pushes are automatically validated:

- Python syntax and import checks
- `manifest.json` and `hacs.json` validation
- Code linting with flake8

### Manual Release

You can also trigger a release manually:

1. Go to Actions tab in GitHub
2. Select "Create Release" workflow
3. Click "Run workflow" button

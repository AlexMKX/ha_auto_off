# ha-test-kit: Static Analysis + Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `ha-test-kit` (git submodule) with ruff lint + format (fail-fast), vulture dead-code analysis (warnings), and pytest-cov coverage for unit and e2e modes — all enabled by default.

**Architecture:** Changes land in two repositories. Upstream `ha-test-kit` gets the harness-side code (Dockerfile, autoqa.py, pyproject.toml, compose, README, whitelist file). The consuming repo `ha_switch_auto_off` bumps the submodule pointer, adds `.autoqa/` to gitignore if missing, and fixes whatever ruff surfaces on first run.

**Tech Stack:** Python 3.13, Home Assistant 2026.1.3, ruff 0.6.x, vulture 2.14, coverage 7.6.x, pytest-cov 5.0.x, Docker Compose.

**Design spec:** `docs/superpowers/specs/2026-04-23-ha-test-kit-static-and-coverage-design.md`.

---

## Phase boundaries

- **Phase A (Tasks 1–11)**: Upstream `ha-test-kit` repo. All commits land in `github.com/AlexMKX/ha-test-kit`. Working directory is the submodule: `ha-test-kit/`. Phase ends with a local major version tag (`v2.0.0`) and a pushed branch.
- **Phase B (Tasks 12–15)**: Consuming `ha_switch_auto_off` repo. Starts only after Phase A is tagged. Working directory is the repo root. Bumps submodule pointer, fixes ruff violations surfaced by the new harness defaults.

Each task ends with a commit and running the relevant verification.

---

## File structure

### Upstream ha-test-kit changes (Phase A)

```
ha-test-kit/
├── Dockerfile.autoqa          # MODIFY: add ruff/vulture/coverage/pytest-cov to pip install
├── docker-compose.yml         # MODIFY: HA env (COVERAGE_PROCESS_START, PYTHONPATH); autoqa .autoqa mount
├── pyproject.toml             # MODIFY: add [tool.ruff], [tool.ruff.lint], [tool.ruff.format], [tool.vulture]
├── run_unit.sh                # MODIFY: mount ./.autoqa as writable subpath
├── README.md                  # MODIFY: add "Static analysis" and "Coverage" sections
├── ha_test_kit/
│   ├── __init__.py            # unchanged
│   ├── autoqa.py              # MODIFY: _run_static_checks, _seed_coverage_bootstrap,
│   │                          #         _finalize_e2e_coverage, coverage flags in _run_pytest,
│   │                          #         main() wiring
│   └── vulture_whitelist.py   # CREATE: HA framework whitelist
├── sitecustomize.py           # unchanged (autoqa-container-side event-loop helper)
└── run_e2e.sh                 # unchanged (compose orchestration stays identical)
```

### Consuming repo changes (Phase B)

```
.gitignore                     # VERIFY: .autoqa/ already present; if not, add
ha-test-kit                    # MODIFY: submodule pointer → new upstream tag
custom_components/auto_off/    # MODIFY: fix ruff violations surfaced by first harness run
                               #          (scope depends on what ruff reports)
```

### Responsibilities per file

- `Dockerfile.autoqa` — provision Python environment with static-analysis and coverage tools.
- `docker-compose.yml` — wire coverage env into HA container and expose `.autoqa` mount to autoqa.
- `pyproject.toml` — single source of truth for ruff/vulture rules applied by the harness.
- `vulture_whitelist.py` — explicit list of HA framework symbols vulture should treat as used.
- `autoqa.py` — dispatcher; adds three new functions and one new `_run_pytest` branch without changing the existing mode state machine.
- `run_unit.sh` — adds writable coverage-output mount; otherwise unchanged.
- `README.md` — documents the new env flags and output locations.

---

## Phase A — Upstream ha-test-kit

All Phase A tasks are executed from the repo root; each Phase A task operates on files under `ha-test-kit/` and commits from **inside** the submodule directory (i.e., `git -C ha-test-kit ...`). The tag is created at the end of Phase A.

### Task 1: Pin new dependencies in Dockerfile

**Files:**
- Modify: `ha-test-kit/Dockerfile.autoqa`

- [ ] **Step 1: Add ruff, vulture, coverage, pytest-cov to pip install**

Current file (lines 19–25):

```dockerfile
RUN python -m pip install \
      "homeassistant==${HA_VERSION}" \
      pytest \
      pytest-asyncio \
      requests \
      websockets \
      playwright
```

Replace with:

```dockerfile
RUN python -m pip install \
      "homeassistant==${HA_VERSION}" \
      pytest \
      pytest-asyncio \
      "pytest-cov==5.0.*" \
      "coverage==7.6.*" \
      "ruff==0.6.*" \
      "vulture==2.14" \
      requests \
      websockets \
      playwright
```

Rationale: four new packages, all pinned to avoid surprise breakage from upstream major bumps. Versions match the spec and current-as-of-2026 best practice.

- [ ] **Step 2: Build the image and verify installation**

Run from the consuming repo root:

```bash
docker build -t autoqa-unit-test -f ha-test-kit/Dockerfile.autoqa ha-test-kit/
docker run --rm autoqa-unit-test python -c "import ruff, vulture, coverage, pytest_cov; print('all imports OK')"
```

Expected: `all imports OK`. The first command may take ~2–3 min (coverage.py has no compile step, ruff/vulture are pure-Python wheels).

Note: `import ruff` fails as a Python module — ruff is a compiled binary. Adjust the check:

```bash
docker run --rm autoqa-unit-test bash -c "ruff --version && vulture --version && coverage --version && python -c 'import pytest_cov'"
```

Expected: four version lines printed, exit 0.

- [ ] **Step 3: Commit (inside the submodule)**

```bash
cd ha-test-kit
git checkout -b feat/static-and-coverage
git add Dockerfile.autoqa
git commit -m "deps: add ruff, vulture, coverage, pytest-cov to autoqa image

Pin versions: ruff==0.6.*, vulture==2.14, coverage==7.6.*,
pytest-cov==5.0.*. Prepares the image for the static-analysis and
coverage features that land in subsequent commits."
cd ..
```

---

### Task 2: Add ruff and vulture configuration to pyproject.toml

**Files:**
- Modify: `ha-test-kit/pyproject.toml`

- [ ] **Step 1: Append ruff + vulture sections**

Current file ends at line 15 with the `markers = [...]` block. Append after it:

```toml

[tool.ruff]
line-length = 120
target-version = "py313"
extend-exclude = [
    ".autoqa",
    ".venv",
    "venv",
    "__pycache__",
]

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes (unused imports, undefined names)
    "I",    # isort (import order)
    "B",    # flake8-bugbear (antipatterns)
    "UP",   # pyupgrade (modern Python)
    "N",    # pep8-naming
    "SIM",  # flake8-simplify
]
ignore = [
    "E501",   # line-too-long handled by ruff format
]

[tool.ruff.lint.per-file-ignores]
"**/tests/**" = ["F401", "F811"]
"custom_components/**" = ["B008"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
docstring-code-format = true

[tool.vulture]
exclude = ["*/tests/*"]
min_confidence = 60
ignore_decorators = ["@pytest.fixture", "@callback", "@staticmethod", "@property"]
sort_by_size = true
```

Note: vulture `paths` is intentionally omitted — `_run_static_checks` computes paths at runtime based on what exists in `/work`.

- [ ] **Step 2: Self-check the config on the harness sources**

Build the image updated in Task 1 is already in the docker cache. Run:

```bash
docker run --rm -v "$(pwd):/work" -w /work autoqa-unit-test \
  ruff check --config /work/ha-test-kit/pyproject.toml /work/ha-test-kit/ha_test_kit
```

Expected: either `All checks passed!` or a list of violations in harness code. If violations appear, fix them in this same commit — they must be zero before the commit lands. Typical first-pass issues: missing `from __future__ import annotations`, unsorted imports (`I001`).

Also verify format:

```bash
docker run --rm -v "$(pwd):/work" -w /work autoqa-unit-test \
  ruff format --check --config /work/ha-test-kit/pyproject.toml /work/ha-test-kit/ha_test_kit
```

If format `--check` exits non-zero, run without `--check` to apply fixes, then commit the formatted result:

```bash
docker run --rm -v "$(pwd):/work" -w /work autoqa-unit-test \
  ruff format --config /work/ha-test-kit/pyproject.toml /work/ha-test-kit/ha_test_kit
```

- [ ] **Step 3: Commit**

```bash
cd ha-test-kit
git add pyproject.toml ha_test_kit/
git commit -m "chore(pyproject): add ruff and vulture configuration

Adds tool.ruff, tool.ruff.lint, tool.ruff.format, and tool.vulture
sections with the rule set defined in the design spec. Vulture paths
are resolved at runtime to keep the config generic across consumers."
cd ..
```

---

### Task 3: Create vulture whitelist for HA framework symbols

**Files:**
- Create: `ha-test-kit/ha_test_kit/vulture_whitelist.py`

- [ ] **Step 1: Write the whitelist file**

```python
"""Whitelist for vulture to suppress HA framework conventions.

Vulture does not understand that these names are called by Home
Assistant itself (config flow dispatcher, entity platform, pydantic
validators, etc.), not by in-repo code. Listing them here marks
them as "used" for vulture's cross-file analysis.

Pattern: any symbol that HA or pydantic calls by convention goes
here. Project-specific dead code should still be caught — do not
add specific function names from the consuming project.
"""

# Home Assistant integration lifecycle
async_setup_entry = None
async_unload_entry = None
async_migrate_entry = None
async_remove_config_entry_device = None
async_setup = None

# HA config flow
VERSION = None
async_step_user = None
async_step_init = None
async_step_import = None
async_get_options_flow = None

# HA entity API
device_info = None
extra_state_attributes = None
async_added_to_hass = None
async_will_remove_from_hass = None
async_set_value = None
native_value = None

# HA entity _attr_* shortcuts
_attr_name = None
_attr_unique_id = None
_attr_is_on = None
_attr_native_value = None
_attr_should_poll = None
_attr_device_class = None
_attr_icon = None
_attr_has_entity_name = None

# pydantic v2
model_config = None
```

- [ ] **Step 2: Sanity-check the whitelist works**

Run vulture on a synthetic input that uses `async_setup_entry`:

```bash
mkdir -p /tmp/vulture_test && cat > /tmp/vulture_test/x.py <<'EOF'
def async_setup_entry(hass, entry):
    return True
EOF

docker run --rm \
  -v /tmp/vulture_test:/tmp/vulture_test \
  -v "$(pwd)/ha-test-kit:/opt/ha_test_kit" \
  autoqa-unit-test \
  vulture --min-confidence 60 /tmp/vulture_test/x.py
```

Expected: output says `async_setup_entry` is unused (confidence 60%).

Now pass the whitelist:

```bash
docker run --rm \
  -v /tmp/vulture_test:/tmp/vulture_test \
  -v "$(pwd)/ha-test-kit:/opt/ha_test_kit" \
  autoqa-unit-test \
  vulture --min-confidence 60 /tmp/vulture_test/x.py /opt/ha_test_kit/ha_test_kit/vulture_whitelist.py
```

Expected: no output about `async_setup_entry` (whitelist hid it). Clean up: `rm -rf /tmp/vulture_test`.

- [ ] **Step 3: Commit**

```bash
cd ha-test-kit
git add ha_test_kit/vulture_whitelist.py
git commit -m "feat(autoqa): add HA framework vulture whitelist

Lists HA/pydantic symbols that vulture cannot detect as used
through cross-file analysis: config flow lifecycle, entity API,
_attr_* shortcuts, pydantic model_config. Consumers do not need
to add project-specific symbols to this file."
cd ..
```

---

### Task 4: Implement `_run_static_checks` in autoqa.py

**Files:**
- Modify: `ha-test-kit/ha_test_kit/autoqa.py`

- [ ] **Step 1: Add `_run_static_checks` function**

Insert this function immediately before `def _install_component_requirements` (currently around line 58):

```python
def _run_static_checks(work_dir: Path) -> None:
    """Run ruff (fail-fast) and vulture (warnings-only) before tests.

    Both checks default to enabled; disable with AUTOQA_LINT=false
    and AUTOQA_VULTURE=false respectively. Paths are computed from
    what exists under work_dir so the harness stays generic.
    """
    pyproject = work_dir / "ha-test-kit" / "pyproject.toml"
    candidate_targets = ["custom_components", "ha-test-kit/ha_test_kit"]
    targets = [t for t in candidate_targets if (work_dir / t).is_dir()]

    if _env_bool("AUTOQA_LINT", default=True):
        if not targets:
            _log("No lint targets found under /work; skipping ruff")
        else:
            _log(f"[ruff] check: {targets}")
            check_rc = subprocess.run(
                [
                    sys.executable, "-m", "ruff", "check",
                    "--config", str(pyproject),
                    *targets,
                ],
                cwd=work_dir,
            ).returncode
            if check_rc != 0:
                raise SystemExit(
                    f"[ruff] check failed with exit code {check_rc}"
                )

            _log("[ruff] format --check")
            fmt_rc = subprocess.run(
                [
                    sys.executable, "-m", "ruff", "format", "--check",
                    "--config", str(pyproject),
                    *targets,
                ],
                cwd=work_dir,
            ).returncode
            if fmt_rc != 0:
                raise SystemExit(
                    f"[ruff-format] check failed with exit code {fmt_rc}"
                )

    if _env_bool("AUTOQA_VULTURE", default=True) and targets:
        whitelist = Path("/opt/ha_test_kit/ha_test_kit/vulture_whitelist.py")
        result = subprocess.run(
            [
                sys.executable, "-m", "vulture",
                "--config", str(pyproject),
                *targets,
                str(whitelist),
            ],
            cwd=work_dir,
            capture_output=True,
            text=True,
        )
        output = result.stdout.strip()
        if output:
            print(
                "[vulture] Potential dead code "
                "(review manually — may contain false positives):",
                flush=True,
            )
            print(output, flush=True)
        else:
            _log("[vulture] No findings")
        # Intentionally ignore exit code: vulture decisions require
        # human judgment.
```

- [ ] **Step 2: Wire into `main()`**

In the existing `main()` function, add the static-check call at the very top (after `mode` and `work_dir` are computed, before `_install_component_requirements`):

Current top of `main()` (around lines 537–542):

```python
def main() -> int:
    mode = os.environ.get("AUTOQA_MODE", "unit").strip().lower()

    work_dir = Path(os.environ.get("AUTOQA_WORK_DIR", "/work"))

    _install_component_requirements(work_dir)
```

Replace with:

```python
def main() -> int:
    mode = os.environ.get("AUTOQA_MODE", "unit").strip().lower()

    work_dir = Path(os.environ.get("AUTOQA_WORK_DIR", "/work"))

    if mode in ("unit", "e2e"):
        _run_static_checks(work_dir)

    _install_component_requirements(work_dir)
```

- [ ] **Step 3: Smoke-test on the consuming repo**

Rebuild and run unit mode from the consuming repo root:

```bash
docker build -t autoqa-unit -f ha-test-kit/Dockerfile.autoqa ha-test-kit/
docker run --rm \
  -v "$(pwd):/work:ro" \
  -e AUTOQA_MODE=unit \
  -e AUTOQA_PYTEST_CONFIG="ha-test-kit/pyproject.toml" \
  autoqa-unit
```

Expected: static checks run first. Likely outcomes:
- `[ruff] check failed` if `custom_components/auto_off/` has violations → this is expected and **handled in Phase B**. To proceed with Phase A testing, temporarily set `AUTOQA_LINT=false`:

```bash
docker run --rm \
  -v "$(pwd):/work:ro" \
  -e AUTOQA_MODE=unit \
  -e AUTOQA_LINT=false \
  -e AUTOQA_PYTEST_CONFIG="ha-test-kit/pyproject.toml" \
  autoqa-unit
```

Expected: `[vulture]` output (harmless noise), then pytest runs normally.

- [ ] **Step 4: Commit**

```bash
cd ha-test-kit
git add ha_test_kit/autoqa.py
git commit -m "feat(autoqa): run ruff + vulture before pytest

Adds _run_static_checks invoked at the top of main() for unit and
e2e modes. ruff check and ruff format --check are fail-fast; vulture
is warnings-only (output printed, exit code ignored). Seed mode is
unaffected. Controlled by AUTOQA_LINT and AUTOQA_VULTURE env flags
(both default true)."
cd ..
```

---

### Task 5: Add unit-mode coverage flags to `_run_pytest`

**Files:**
- Modify: `ha-test-kit/ha_test_kit/autoqa.py`

- [ ] **Step 1: Inject coverage flags when coverage is enabled in unit mode**

Locate the existing `_run_pytest` function (around line 469). Find the `if mode == "unit":` branch inside it (around line 504):

```python
    if mode == "unit":
        args += ["-m", "not docker_e2e"]
    elif mode == "e2e":
        args += ["-m", "docker_e2e"]
    else:
        raise RuntimeError(f"Unknown AUTOQA_MODE: {mode}")
```

Replace with:

```python
    if mode == "unit":
        args += ["-m", "not docker_e2e"]
        if _env_bool("AUTOQA_COVERAGE", default=True):
            cov_out = work_dir / ".autoqa" / "coverage" / "unit"
            cov_out.mkdir(parents=True, exist_ok=True)
            args += [
                "--cov=custom_components",
                "--cov-branch",
                "--cov-report=term-missing",
                f"--cov-report=html:{cov_out / 'html'}",
                f"--cov-report=xml:{cov_out / 'coverage.xml'}",
            ]
            extra_env["COVERAGE_FILE"] = str(cov_out / ".coverage")
    elif mode == "e2e":
        args += ["-m", "docker_e2e"]
    else:
        raise RuntimeError(f"Unknown AUTOQA_MODE: {mode}")
```

Note: `extra_env` is already a dict passed into `_run_pytest`. Adding `COVERAGE_FILE` to it propagates to the subprocess via the existing `env = dict(os.environ); env.update(extra_env)` below.

- [ ] **Step 2: Verify in unit mode**

From the consuming repo root, temporarily bypass lint to test coverage in isolation:

```bash
mkdir -p .autoqa/coverage
docker build -t autoqa-unit -f ha-test-kit/Dockerfile.autoqa ha-test-kit/
docker run --rm \
  -v "$(pwd):/work" \
  -e AUTOQA_MODE=unit \
  -e AUTOQA_LINT=false \
  -e AUTOQA_PYTEST_CONFIG="ha-test-kit/pyproject.toml" \
  autoqa-unit
```

Note: `-v "$(pwd):/work"` (writable) instead of `:ro` is needed because coverage writes files under `.autoqa/coverage/unit/`. Task 7 will update `run_unit.sh` to handle this with an overlapping mount; the command above is a one-off for Task 5 verification.

Expected after run:
- `.autoqa/coverage/unit/.coverage` exists (binary).
- `.autoqa/coverage/unit/coverage.xml` exists and parses as XML:

```bash
python -c "import xml.etree.ElementTree as ET; ET.parse('.autoqa/coverage/unit/coverage.xml'); print('xml ok')"
```

- `.autoqa/coverage/unit/html/index.html` exists.
- Terminal output includes a coverage table with `custom_components/auto_off/...` rows.

- [ ] **Step 3: Commit**

```bash
cd ha-test-kit
git add ha_test_kit/autoqa.py
git commit -m "feat(autoqa): unit-mode pytest-cov integration

Injects --cov/--cov-branch/--cov-report flags into unit pytest when
AUTOQA_COVERAGE=true (default). Reports land in
.autoqa/coverage/unit/: raw .coverage, coverage.xml (Cobertura),
html/ (interactive), and term-missing in stdout. COVERAGE_FILE env
keeps raw data next to the reports."
cd ..
```

---

### Task 6: Seed-phase coverage bootstrap

**Files:**
- Modify: `ha-test-kit/ha_test_kit/autoqa.py`

- [ ] **Step 1: Add the two template strings near the top of autoqa.py**

After the existing `_MINIMAL_HA_CONFIG` string (around line 37), add:

```python
_COVERAGERC_CONTENTS = """\
[run]
branch = True
parallel = True
concurrency = thread,asyncio
source = /config/custom_components
data_file = /config/coverage-data/.coverage

[report]
exclude_also =
    if TYPE_CHECKING:
    raise NotImplementedError
    \\.\\.\\.

[paths]
source =
    /config/custom_components
    custom_components
"""

_SITECUSTOMIZE_CONTENTS = '''\
"""Coverage bootstrap loaded by CPython via the site module.

Present in the HA container via PYTHONPATH=/config/.coverage_pkg.
Python imports sitecustomize automatically when it appears on sys.path.
coverage.process_startup() reads COVERAGE_PROCESS_START from env,
starts tracing, and registers an atexit hook to persist data.
"""
import os

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage
    except ModuleNotFoundError:
        # Expected when AUTOQA_COVERAGE=false: seed skipped bootstrap,
        # but docker-compose env still points COVERAGE_PROCESS_START
        # at a non-existent file. Silent no-op.
        pass
    else:
        coverage.process_startup()
'''
```

Note the double-escaped `\\.\\.\\.` inside `_COVERAGERC_CONTENTS`: the ini file expects a literal `\.\.\.` regex, and we're writing it through a Python string so each backslash is doubled.

- [ ] **Step 2: Add `_seed_coverage_bootstrap` function**

Insert after the existing `_seed_ha_configuration` function (currently ends around line 228):

```python
def _seed_coverage_bootstrap(ha_config_dir: Path) -> None:
    """Install coverage.py and sitecustomize into the HA config volume.

    Runs inside the seed container, which has pip and network access.
    The HA container then loads these artefacts through PYTHONPATH and
    COVERAGE_PROCESS_START without needing coverage pre-installed in
    its own site-packages.
    """
    if not _env_bool("AUTOQA_COVERAGE", default=True):
        _log("AUTOQA_COVERAGE=false; skipping coverage bootstrap")
        return

    pkg_dir = ha_config_dir / ".coverage_pkg"
    data_dir = ha_config_dir / "coverage-data"
    coveragerc = ha_config_dir / ".coveragerc"
    sitecustomize = pkg_dir / "sitecustomize.py"

    pkg_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Installing coverage into {pkg_dir}")
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--no-input", "--no-cache-dir",
            "--target", str(pkg_dir),
            "coverage==7.6.*",
        ],
        check=True,
    )

    coveragerc.write_text(_COVERAGERC_CONTENTS, encoding="utf-8")
    sitecustomize.write_text(_SITECUSTOMIZE_CONTENTS, encoding="utf-8")
    _log(f"Coverage bootstrap ready at {pkg_dir}, rc={coveragerc}")
```

- [ ] **Step 3: Call bootstrap from `main()` for `mode=seed` and `mode=e2e`**

The current `main()` has two branches that call `_seed_ha_configuration`: the `seed` branch (around line 544) and the `e2e` branch (around line 551). Add a `_seed_coverage_bootstrap` call after each:

In the `seed` branch, replace:

```python
    if mode == "seed":
        ha_config_dir = Path(os.environ.get("HA_CONFIG_DIR", "/ha_config"))
        _seed_ha_configuration(ha_config_dir, work_dir)
        _touch_ready_flag(ha_config_dir)
        return 0
```

With:

```python
    if mode == "seed":
        ha_config_dir = Path(os.environ.get("HA_CONFIG_DIR", "/ha_config"))
        _seed_ha_configuration(ha_config_dir, work_dir)
        _seed_coverage_bootstrap(ha_config_dir)
        _touch_ready_flag(ha_config_dir)
        return 0
```

In the `e2e` branch, replace:

```python
    if mode == "e2e":
        ha_config_dir = Path(os.environ.get("HA_CONFIG_DIR", "/ha_config"))
        _seed_ha_configuration(ha_config_dir, work_dir)
        _touch_ready_flag(ha_config_dir)
```

With:

```python
    if mode == "e2e":
        ha_config_dir = Path(os.environ.get("HA_CONFIG_DIR", "/ha_config"))
        _seed_ha_configuration(ha_config_dir, work_dir)
        _seed_coverage_bootstrap(ha_config_dir)
        _touch_ready_flag(ha_config_dir)
```

(The `e2e` mode is called via Docker Compose where the `seed` service runs first, so the `e2e` branch rarely re-runs both. Having bootstrap in both is defensive.)

- [ ] **Step 4: Sanity-check seed mode in isolation**

```bash
docker build -t autoqa-unit -f ha-test-kit/Dockerfile.autoqa ha-test-kit/
docker volume create ha_config_test
docker run --rm \
  -v "$(pwd):/work:ro" \
  -v ha_config_test:/ha_config \
  -e AUTOQA_MODE=seed \
  -e HA_CONFIG_DIR=/ha_config \
  -e AUTOQA_FORCE_SEED=true \
  autoqa-unit
```

Inspect the volume:

```bash
docker run --rm -v ha_config_test:/ha_config alpine ls -la /ha_config/.coverage_pkg /ha_config/.coveragerc /ha_config/coverage-data
```

Expected: `.coveragerc` exists, `.coverage_pkg/sitecustomize.py` exists, `.coverage_pkg/coverage/` directory exists with the coverage package inside, `coverage-data/` is an empty dir.

Clean up: `docker volume rm ha_config_test`.

- [ ] **Step 5: Commit**

```bash
cd ha-test-kit
git add ha_test_kit/autoqa.py
git commit -m "feat(autoqa): seed-phase coverage bootstrap for e2e

Installs coverage.py via pip install --target into /ha_config/.coverage_pkg
(shared with the HA container), writes /ha_config/.coveragerc and
sitecustomize.py. HA container loads sitecustomize automatically at
Python startup when /config/.coverage_pkg is on PYTHONPATH and
COVERAGE_PROCESS_START env is set (wired in docker-compose in the
next commit)."
cd ..
```

---

### Task 7: Add `_finalize_e2e_coverage` and wire into e2e main()

**Files:**
- Modify: `ha-test-kit/ha_test_kit/autoqa.py`

- [ ] **Step 1: Ensure `shutil` and `time` are imported**

Check the top of `autoqa.py`. `time` is already imported (line 10). Verify `shutil` is imported (line 6). If either is missing, add it.

- [ ] **Step 2: Add `_finalize_e2e_coverage` function**

Insert after the existing `_run_pytest` function (currently ends around line 534):

```python
def _finalize_e2e_coverage(
    base_url: str,
    access_token: str,
    ha_config_dir: Path,
    out_dir: Path,
) -> None:
    """Stop HA gracefully, wait for .coverage.* fragments, combine them.

    autoqa must not exit before HA has flushed its coverage data to
    the shared volume; otherwise compose's --abort-on-container-exit
    tears down the HA container before its atexit hook runs. This
    function coordinates the shutdown explicitly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = ha_config_dir / "coverage-data"

    _log("[coverage] Requesting HA shutdown via core.stop")
    try:
        requests.post(
            f"{base_url}/api/services/homeassistant/stop",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"[coverage] HA core.stop failed: {exc}") from exc

    deadline = time.time() + 30
    fragments: list[Path] = []
    while time.time() < deadline:
        fragments = list(data_dir.glob(".coverage.*"))
        if fragments:
            break
        time.sleep(0.5)

    if not fragments:
        raise RuntimeError(
            f"[coverage] No .coverage.* fragments appeared in {data_dir} "
            "within 30s of HA core.stop. Check that COVERAGE_PROCESS_START "
            "is set on the homeassistant service and that "
            "/config/.coverage_pkg/sitecustomize.py is importable."
        )

    # Grace period for late writes.
    time.sleep(2)

    for frag in data_dir.glob(".coverage.*"):
        shutil.copyfile(frag, out_dir / frag.name)

    _log(f"[coverage] Combining {len(fragments)} fragment(s) into {out_dir}")
    cov_env = {**os.environ, "COVERAGE_RCFILE": str(ha_config_dir / ".coveragerc")}
    subprocess.run(
        [sys.executable, "-m", "coverage", "combine"],
        cwd=out_dir, check=True, env=cov_env,
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "report", "--show-missing"],
        cwd=out_dir, check=True, env=cov_env,
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "html",
         "-d", str(out_dir / "html")],
        cwd=out_dir, check=True, env=cov_env,
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "xml",
         "-o", str(out_dir / "coverage.xml")],
        cwd=out_dir, check=True, env=cov_env,
    )
```

- [ ] **Step 3: Wire into e2e branch of `main()`**

Locate the e2e branch (around line 604) where it currently ends:

```python
        return _run_pytest(mode=mode, extra_env=extra_env)
```

Replace with:

```python
        pytest_rc = _run_pytest(mode=mode, extra_env=extra_env)

        if _env_bool("AUTOQA_COVERAGE", default=True):
            _finalize_e2e_coverage(
                base_url=base_url,
                access_token=llat,
                ha_config_dir=ha_config_dir,
                out_dir=work_dir / ".autoqa" / "coverage" / "e2e",
            )

        return pytest_rc
```

Rationale: `llat` is the long-lived access token created above in the same `main()`. Unlike the short-lived `access_token`, it survives long e2e runs. Per design spec Section 3.4.

- [ ] **Step 4: Smoke-test e2e (best-effort — full e2e requires HA container)**

Running the full e2e flow requires the compose stack which depends on `docker-compose.yml` changes from Task 8. Defer the end-to-end verification to Task 11 (integration test). For this task, verify the code parses and imports cleanly:

```bash
docker run --rm -v "$(pwd):/work:ro" autoqa-unit \
  python -c "from ha_test_kit import autoqa; print('OK')"
```

Expected: `OK`. Any syntax error in the new function surfaces here.

- [ ] **Step 5: Commit**

```bash
cd ha-test-kit
git add ha_test_kit/autoqa.py
git commit -m "feat(autoqa): e2e coverage finalize with SIGTERM coordination

Adds _finalize_e2e_coverage called after pytest in e2e mode when
AUTOQA_COVERAGE=true. Explicitly requests HA shutdown via core.stop,
polls /ha_config/coverage-data for .coverage.* fragments (30s
budget, 2s grace), then runs coverage combine / report / html / xml
with repo-relative paths via .coveragerc [paths] remap. Fail-fast:
missing fragments or combine failure raises RuntimeError."
cd ..
```

---

### Task 8: Update docker-compose.yml with coverage env + writable mount

**Files:**
- Modify: `ha-test-kit/docker-compose.yml`

- [ ] **Step 1: Add coverage env to `homeassistant` service**

Current homeassistant service block (lines 23–32):

```yaml
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:${HA_VERSION:-2025.1.3}
    restart: "no"
    volumes:
      - ha_config:/config
      - ./custom_components:/config/custom_components:ro
    environment:
      - TZ=UTC
    networks:
      - e2e_network
```

Replace the `environment:` block:

```yaml
    environment:
      - TZ=UTC
      - COVERAGE_PROCESS_START=/config/.coveragerc
      - PYTHONPATH=/config/.coverage_pkg
```

The two new env vars are set unconditionally. When `AUTOQA_COVERAGE=false`, the seed phase doesn't create `.coveragerc` or `.coverage_pkg/`, and `sitecustomize.py` is never loaded (PYTHONPATH points at a non-existent directory, which Python ignores).

- [ ] **Step 2: Add writable `.autoqa` mount to `autoqa` service**

Current autoqa service volumes (lines 42–46):

```yaml
    volumes:
      - .:/work:ro
      - ha_config:/ha_config
      - ./.autoqa/test_results:/test_results
      - ./.autoqa/screenshots:/screenshots
```

Replace with:

```yaml
    volumes:
      - .:/work:ro
      - ./.autoqa:/work/.autoqa
      - ha_config:/ha_config
      - ./.autoqa/test_results:/test_results
      - ./.autoqa/screenshots:/screenshots
```

The new line `./.autoqa:/work/.autoqa` overlays a writable subpath on top of the read-only `/work` mount. Docker handles overlapping mounts correctly: the inner mount wins for its subtree.

- [ ] **Step 3: Verify compose YAML is valid**

```bash
docker compose -f ha-test-kit/docker-compose.yml --project-directory . config > /dev/null
```

Expected: no output, exit 0. Any YAML error surfaces here.

- [ ] **Step 4: Commit**

```bash
cd ha-test-kit
git add docker-compose.yml
git commit -m "chore(compose): wire coverage env + writable .autoqa mount

homeassistant service gets COVERAGE_PROCESS_START and PYTHONPATH so
sitecustomize.py from /config/.coverage_pkg loads at Python startup.
autoqa service gets a writable ./.autoqa:/work/.autoqa mount so
coverage finalize can write reports back to the host without
fighting the read-only /work mount."
cd ..
```

---

### Task 9: Update run_unit.sh with writable .autoqa mount

**Files:**
- Modify: `ha-test-kit/run_unit.sh`

- [ ] **Step 1: Add writable `.autoqa` mount**

Current docker run invocation (lines 15–21):

```bash
docker run --rm \
  -v "${PROJECT_ROOT}:/work:ro" \
  -e AUTOQA_MODE=unit \
  -e AUTOQA_PYTEST_CONFIG="ha-test-kit/pyproject.toml" \
  -e AUTOQA_TEST_PATHS="${AUTOQA_TEST_PATHS:-}" \
  -e AUTOQA_PYTEST_ARGS="${AUTOQA_PYTEST_ARGS:-}" \
  autoqa-unit
```

Replace with:

```bash
mkdir -p "${PROJECT_ROOT}/.autoqa/coverage"

docker run --rm \
  -v "${PROJECT_ROOT}:/work:ro" \
  -v "${PROJECT_ROOT}/.autoqa:/work/.autoqa" \
  -e AUTOQA_MODE=unit \
  -e AUTOQA_PYTEST_CONFIG="ha-test-kit/pyproject.toml" \
  -e AUTOQA_TEST_PATHS="${AUTOQA_TEST_PATHS:-}" \
  -e AUTOQA_PYTEST_ARGS="${AUTOQA_PYTEST_ARGS:-}" \
  -e AUTOQA_LINT="${AUTOQA_LINT:-}" \
  -e AUTOQA_VULTURE="${AUTOQA_VULTURE:-}" \
  -e AUTOQA_COVERAGE="${AUTOQA_COVERAGE:-}" \
  autoqa-unit
```

Three changes:
1. `mkdir -p` ensures the host directory exists before mounting (otherwise Docker creates it as root-owned).
2. New volume mount for `.autoqa`.
3. Three new env-var passthroughs for `AUTOQA_LINT`, `AUTOQA_VULTURE`, `AUTOQA_COVERAGE`. Empty string passthrough is safe: `_env_bool` returns the default when the env is unset or empty.

- [ ] **Step 2: Verify the script runs**

```bash
chmod +x ha-test-kit/run_unit.sh
AUTOQA_LINT=false ./ha-test-kit/run_unit.sh
```

Expected: docker build + run succeeds; `.autoqa/coverage/unit/` populated after the run (unit tests expected to pass since nothing else changed).

- [ ] **Step 3: Commit**

```bash
cd ha-test-kit
git add run_unit.sh
git commit -m "chore(run_unit): writable .autoqa mount + AUTOQA_* env passthrough

Coverage reports written by pytest-cov need a writable destination
on the host. Also passes AUTOQA_LINT, AUTOQA_VULTURE, AUTOQA_COVERAGE
through to the container so callers can override per-invocation."
cd ..
```

---

### Task 10: Update README with new sections

**Files:**
- Modify: `ha-test-kit/README.md`

- [ ] **Step 1: Add "Static analysis" and "Coverage" sections**

Read the current README structure. Insert two new top-level sections between "How it works (high level)" and "Repo conventions the harness relies on". The new sections cover the three features documented in the spec.

Add this content immediately after the "How it works (high level)" section (currently ends with the `---` after step 7):

```markdown
---

## Static analysis (ruff + vulture)

Every unit and e2e run begins with two static checks, both enabled by default.

**ruff** (fail-fast): lint + format check on `custom_components/` and
`ha-test-kit/ha_test_kit/`. Violations block the rest of the run.
Configuration lives in `ha-test-kit/pyproject.toml` under
`[tool.ruff]` / `[tool.ruff.lint]` / `[tool.ruff.format]`. The rule
set is `E, W, F, I, B, UP, N, SIM` with `E501` delegated to the
formatter.

**vulture** (warnings-only): dead-code analysis on the same scope.
Output is prefixed with `[vulture]` in stdout and never blocks the
run — vulture produces false positives on HA framework conventions
(async_setup_entry, _attr_*, etc.), most of which are suppressed by
a built-in whitelist at `ha-test-kit/ha_test_kit/vulture_whitelist.py`.
Consumer-specific dead code still surfaces.

Disable independently:

```bash
AUTOQA_LINT=false ./ha-test-kit/run_unit.sh
AUTOQA_VULTURE=false ./ha-test-kit/run_unit.sh
```

To auto-fix ruff issues locally (not invoked by the harness):

```bash
docker run --rm -v "$(pwd):/work" -w /work autoqa-unit \
  ruff check --fix --config /work/ha-test-kit/pyproject.toml custom_components
docker run --rm -v "$(pwd):/work" -w /work autoqa-unit \
  ruff format --config /work/ha-test-kit/pyproject.toml custom_components
```

---

## Coverage

pytest-cov is enabled by default for both unit and e2e modes.
Reports land in `.autoqa/coverage/` on the host.

**Unit mode** — pytest-cov runs inside the autoqa container,
instrumenting `custom_components/` during the normal pytest
invocation. Output:

- `.autoqa/coverage/unit/.coverage` — raw coverage data
- `.autoqa/coverage/unit/coverage.xml` — Cobertura XML (for CI)
- `.autoqa/coverage/unit/html/index.html` — interactive HTML report
- Terminal: `term-missing` summary with uncovered line numbers

**E2E mode** — the integration code runs inside the Home Assistant
container, not inside autoqa. Coverage is collected there via a
three-part mechanism:

1. **seed phase**: installs `coverage==7.6.*` into
   `/ha_config/.coverage_pkg` on a shared volume and writes
   `.coveragerc` + `sitecustomize.py`.
2. **HA runtime**: `docker-compose.yml` sets
   `PYTHONPATH=/config/.coverage_pkg` and
   `COVERAGE_PROCESS_START=/config/.coveragerc` on the `homeassistant`
   service. Python's `site` module imports `sitecustomize.py` at
   startup, which calls `coverage.process_startup()` — tracing begins
   before any HA code runs.
3. **finalize**: after pytest completes, autoqa calls HA's
   `/api/services/homeassistant/stop` to trigger a graceful shutdown,
   polls `/ha_config/coverage-data/` for the `.coverage.*` fragments
   HA writes on exit, then runs `coverage combine` + generates
   HTML/XML reports.

Output locations mirror unit mode: `.autoqa/coverage/e2e/` instead of
`.../unit/`. The `[paths]` remap in `.coveragerc` rewrites HA's
`/config/custom_components/...` paths to the consumer's repo-relative
`custom_components/...` so reports are readable from the host.

**Disable:**

```bash
AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh
AUTOQA_COVERAGE=false ./ha-test-kit/run_e2e.sh
```

**Merge unit + e2e into a single combined report** (not automated):

```bash
coverage combine \
  .autoqa/coverage/unit/.coverage \
  .autoqa/coverage/e2e/.coverage
coverage html -d .autoqa/coverage/combined/html
```

**Failure modes** (all fail-fast when coverage is on):

- Zero `.coverage.*` fragments after HA shutdown → `RuntimeError`,
  typically caused by missing `COVERAGE_PROCESS_START` env.
- `coverage combine` failure → `CalledProcessError` from coverage.
- HA unreachable via `core.stop` → `RuntimeError`.

If pytest also failed in the same run, the coverage error wins — it
signals infrastructure breakage, which invalidates every test result.

---
```

- [ ] **Step 2: Add env flags to the env reference section**

Find the "Environment variables reference → Core" section (starts around line 182 of the current README). Append after the existing `AUTOQA_ENABLE_HA_PYTEST_PLUGIN` entry:

```markdown
- `AUTOQA_LINT`
  - Default: `true`. Set to `false` to skip ruff check and format.

- `AUTOQA_VULTURE`
  - Default: `true`. Set to `false` to skip vulture.

- `AUTOQA_COVERAGE`
  - Default: `true`. Set to `false` to skip pytest-cov in unit and
    coverage bootstrap + finalize in e2e.
```

- [ ] **Step 3: Commit**

```bash
cd ha-test-kit
git add README.md
git commit -m "docs: document static analysis and coverage features

Adds Static Analysis and Coverage sections explaining the three
new env flags (AUTOQA_LINT, AUTOQA_VULTURE, AUTOQA_COVERAGE) and
the three-part e2e coverage mechanism (seed bootstrap + HA-runtime
sitecustomize + autoqa finalize). Documents output locations,
failure modes, and the unit+e2e merge workflow."
cd ..
```

---

### Task 11: End-to-end verification and major version tag

**Files:** none modified; this is a verification-only task.

- [ ] **Step 1: Run unit mode fully (lint on)**

From the consuming repo root:

```bash
./ha-test-kit/run_unit.sh
```

Expected outcomes:
- If `custom_components/auto_off/` has ruff violations: exits non-zero with `[ruff] check failed`. This is expected and will be fixed in Phase B. Proceed with `AUTOQA_LINT=false` for the rest of this task.
- With `AUTOQA_LINT=false`: `[vulture]` output, pytest runs, coverage reports land in `.autoqa/coverage/unit/`.

Verify all three report formats:

```bash
ls -la .autoqa/coverage/unit/
test -s .autoqa/coverage/unit/.coverage && echo "raw ok"
python -c "import xml.etree.ElementTree as ET; ET.parse('.autoqa/coverage/unit/coverage.xml'); print('xml ok')"
test -f .autoqa/coverage/unit/html/index.html && echo "html ok"
```

Expected: three `ok` lines.

- [ ] **Step 2: Run e2e mode (lint off for now)**

```bash
AUTOQA_LINT=false ./ha-test-kit/run_e2e.sh
```

This runs the full docker-compose stack. Expected timeline:
- ~30s: seed container prepares /ha_config + coverage bootstrap.
- ~1min: HA container starts, autoqa provisions auth tokens, pytest runs.
- After pytest: `[coverage]` logs from autoqa, HA shuts down gracefully, combine completes.
- Exit 0 if e2e tests pass.

Verify coverage output:

```bash
ls -la .autoqa/coverage/e2e/
test -s .autoqa/coverage/e2e/.coverage && echo "raw ok"
python -c "import xml.etree.ElementTree as ET; ET.parse('.autoqa/coverage/e2e/coverage.xml'); print('xml ok')"
test -f .autoqa/coverage/e2e/html/index.html && echo "html ok"
grep -c "custom_components/auto_off" .autoqa/coverage/e2e/coverage.xml
```

Expected: three `ok` lines + the grep returns a count > 0 (proving the [paths] remap worked and reports contain repo-relative paths).

- [ ] **Step 3: Verify disable flags**

```bash
rm -rf .autoqa/coverage
AUTOQA_COVERAGE=false AUTOQA_LINT=false AUTOQA_VULTURE=false ./ha-test-kit/run_unit.sh
```

Expected:
- No `[vulture]` output.
- No `--cov=...` args passed to pytest.
- `.autoqa/coverage/` does not appear.
- Tests still run and pass.

- [ ] **Step 4: Verify lint fail-fast**

Temporarily introduce a ruff violation:

```bash
echo "import os, sys" >> custom_components/auto_off/auto_off.py
./ha-test-kit/run_unit.sh
```

Expected: exit non-zero with `[ruff] check failed`. Pytest does not run.

Undo:

```bash
git -C . checkout custom_components/auto_off/auto_off.py
```

- [ ] **Step 5: Verify vulture is warnings-only**

vulture output appears with `[vulture]` prefix in Step 1 logs. Confirm the prior acceptance run (Step 1 with `AUTOQA_LINT=false`) exited 0 even when vulture had findings.

- [ ] **Step 6: Tag the upstream submodule**

All acceptance items from design spec Section 5 / 7 (items 1–7 on upstream side) now verified.

```bash
cd ha-test-kit
git log --oneline
# Verify 10 new commits on the feat/static-and-coverage branch.

git tag -a v2.0.0 -m "Major version: static analysis + coverage by default

Breaking changes:
- ruff check + format run before pytest (fail-fast if violations)
- vulture runs before pytest (warnings to stdout)
- pytest-cov runs by default in unit mode
- coverage instrumentation runs by default in HA container during e2e

All three features opt-out via AUTOQA_LINT / AUTOQA_VULTURE /
AUTOQA_COVERAGE env vars (default true).

See docs/superpowers/specs/2026-04-23-ha-test-kit-static-and-coverage-design.md
in consuming repositories for the full design."

git push origin feat/static-and-coverage
git push origin v2.0.0
cd ..
```

- [ ] **Step 7: No commit in the submodule on this task**

This task runs verifications and creates a tag; no source files change. Phase A is complete.

---

## Phase B — Consuming repo (ha_switch_auto_off)

Phase B tasks run in the consuming repo and assume Phase A is tagged at `v2.0.0`.

### Task 12: Bump submodule pointer to v2.0.0

**Files:**
- Modify: `ha-test-kit` (submodule pointer in the parent repo)

- [ ] **Step 1: Update submodule to the new tag**

From the consuming repo root:

```bash
cd ha-test-kit
git fetch --tags
git checkout v2.0.0
cd ..
```

Verify:

```bash
git -C ha-test-kit describe --tags
# Expected: v2.0.0

git status
# Expected: "Changes not staged for commit:  modified: ha-test-kit (new commits)"
```

- [ ] **Step 2: Verify .autoqa is gitignored**

```bash
grep -q "^\.autoqa" .gitignore && echo "already ignored" || echo ".autoqa MISSING from .gitignore"
```

Current file has `.autoqa/` (verified earlier). If the check reports missing, add the line:

```bash
echo "" >> .gitignore
echo "# ha-test-kit coverage and test artefacts" >> .gitignore
echo ".autoqa/" >> .gitignore
```

- [ ] **Step 3: Commit the submodule bump**

```bash
git add ha-test-kit .gitignore
git commit -m "chore: bump ha-test-kit to v2.0.0

Major version: static analysis + coverage run by default.
See ha-test-kit/README.md for new AUTOQA_LINT, AUTOQA_VULTURE,
AUTOQA_COVERAGE env flags.

Ruff violations surfaced on first run are addressed in follow-up
commits."
```

---

### Task 13: Survey ruff violations and categorize

**Files:** none modified; this is a discovery-only task.

- [ ] **Step 1: Run ruff check and capture output**

```bash
./ha-test-kit/run_unit.sh 2>&1 | tee /tmp/ruff_first_run.log
```

Expected: exits non-zero with `[ruff] check failed with exit code 1`. The log contains the full list of violations.

- [ ] **Step 2: Count violations by rule**

```bash
grep -oE "[A-Z][0-9]+" /tmp/ruff_first_run.log | sort | uniq -c | sort -rn
```

Expected output format (numbers will vary):

```
     12 I001
      8 UP006
      5 F401
      3 B008
      ...
```

- [ ] **Step 3: Identify auto-fixable violations**

Scope: `custom_components/` only. The harness under
`ha-test-kit/ha_test_kit/` is the submodule's responsibility and was
clean when Phase A tagged:

```bash
docker run --rm -v "$(pwd):/work" -w /work autoqa-unit \
  ruff check --fix --config /work/ha-test-kit/pyproject.toml \
  custom_components 2>&1 | tail -20
```

Expected: output shows how many violations were fixed vs. need manual attention.

Also check `ruff format`:

```bash
docker run --rm -v "$(pwd):/work" -w /work autoqa-unit \
  ruff format --check --config /work/ha-test-kit/pyproject.toml \
  custom_components 2>&1 | head
```

Expected: list of files that would be reformatted (or `All files already formatted`).

- [ ] **Step 4: Plan fix strategy**

At this point no files have been modified (the `--fix` in Step 3 was interactive exploration; any changes were on the mounted live volume). Record the findings in a scratch file:

```bash
cat > /tmp/ruff_plan.md <<EOF
# Ruff fix plan (pre-commit)

## Auto-fixable (run ruff check --fix)
- [list rules from Step 3]

## Manual fixes required
- [list rules that --fix did not handle]

## Format-only
- [files listed by ruff format --check]
EOF
```

This task does **not** commit anything. It produces the plan for Task 14.

- [ ] **Step 5: Revert any unintended changes from exploration**

```bash
git status
# If ruff --fix modified files during Step 3, revert:
git checkout -- custom_components
```

---

### Task 14: Apply ruff auto-fixes

**Files:**
- Modify: files listed in `/tmp/ruff_plan.md` from Task 13.

- [ ] **Step 1: Run ruff auto-fix and format on consumer code only**

Phase B touches only the consuming repo. The harness code under
`ha-test-kit/ha_test_kit/` belongs to the submodule and was already
lint-clean when Phase A tagged v2.0.0. Target only `custom_components/`
here:

```bash
docker run --rm -v "$(pwd):/work" -w /work autoqa-unit \
  ruff check --fix --config /work/ha-test-kit/pyproject.toml \
  custom_components

docker run --rm -v "$(pwd):/work" -w /work autoqa-unit \
  ruff format --config /work/ha-test-kit/pyproject.toml \
  custom_components
```

- [ ] **Step 2: Run the test suite to catch regressions**

```bash
AUTOQA_LINT=false ./ha-test-kit/run_unit.sh
```

Expected: all 49 tests pass (auto-fixes should be semantically neutral — import sorting, quote normalization, etc.).

If tests fail: revert and investigate which fix broke semantics:

```bash
git diff custom_components/auto_off
```

Most likely candidates: `UP` rules rewriting `Dict[str, X]` → `dict[str, X]` in pydantic models (sometimes breaks with specific Python versions), or `SIM` rules rewriting conditional logic incorrectly.

- [ ] **Step 3: Commit auto-fixes**

```bash
git add custom_components
git diff --cached --stat
git commit -m "refactor: apply ruff auto-fixes to custom_components

Applied ruff check --fix + ruff format using the v2.0.0 harness
config. Changes are semantically neutral (import sorting, quote
style, simple modernizations). Unit tests still pass."
```

---

### Task 15: Fix remaining ruff violations and verify green harness

**Files:** variable — whatever ruff still reports.

- [ ] **Step 1: Run ruff check to see remaining violations**

```bash
./ha-test-kit/run_unit.sh 2>&1 | tee /tmp/ruff_second_run.log
```

Possible outcomes:
- **Exit 0, all tests pass** → skip to Step 4 (commit verification result) and mark task DONE.
- **Exit non-zero with violations** → proceed to Step 2.

- [ ] **Step 2: Fix each remaining violation manually**

For each rule code in the log, read the ruff docs (e.g., `ruff rule B008`). Fix the code. Common patterns:

- `B008` (mutable default arg): usually in HA service handlers. Already ignored in `custom_components/**` via `per-file-ignores`. If it appears in the harness itself, add `noqa: B008` with a short justification comment or fix the signature.
- `UP` rules: modernize syntax (e.g., `List[X]` → `list[X]`, `Optional[X]` → `X | None`).
- `N` naming: rare in HA code; rename or `noqa: N802` with justification.
- `SIM` simplify: usually safe to apply the suggested rewrite.

For each fix, add a commit once the specific rule is clear — makes review easier:

```bash
git add <files>
git commit -m "refactor: address ruff <RULE> in <scope>

<Brief explanation of the change.>"
```

- [ ] **Step 3: Re-run ruff and iterate**

```bash
./ha-test-kit/run_unit.sh
```

Repeat Steps 1–2 until the harness exits 0.

- [ ] **Step 4: Full harness green run**

```bash
./ha-test-kit/run_unit.sh
```

Expected:
- `[ruff] check: ['custom_components', 'ha-test-kit/ha_test_kit']` → no violations.
- `[ruff] format --check` → no reformatting needed.
- `[vulture]` output printed, harness continues.
- `49 passed, 2 skipped` (or updated count) from pytest.
- Coverage reports in `.autoqa/coverage/unit/`.
- Exit 0.

- [ ] **Step 5: Optional — run e2e to verify coverage pipeline end-to-end**

```bash
./ha-test-kit/run_e2e.sh
```

Expected: full stack comes up, e2e tests pass, `.autoqa/coverage/e2e/` populated with `.coverage`, `coverage.xml`, `html/`. Skip if Docker Compose e2e is not available in the current environment.

- [ ] **Step 6: Commit the verification result**

This task may have already committed individual fix commits in Step 2. If additional changes accumulated after the last Step 2 commit (e.g., README tweaks), commit them now:

```bash
git status
# If clean: nothing to commit, task is done.
# If dirty: commit with a descriptive message per change.
```

Implementation is complete when `./ha-test-kit/run_unit.sh` exits 0 with coverage reports populated.

---

## Self-review notes

Checked against the design spec `docs/superpowers/specs/2026-04-23-ha-test-kit-static-and-coverage-design.md`:

- **Section 1 (static analysis)**: Tasks 1, 2, 3, 4, 10 cover Dockerfile deps, pyproject config, whitelist, autoqa wiring, README docs. ✅
- **Section 2 (unit coverage)**: Task 5 adds pytest-cov flags; Task 9 handles the writable mount. ✅
- **Section 3 (e2e coverage)**: Task 6 handles seed bootstrap; Task 7 handles finalize; Task 8 handles compose env + mount; Task 10 documents the mechanism. ✅
- **Section 4 (error handling)**: Fail-fast semantics are in the code from Tasks 4, 6, 7. ✅
- **Section 5 (testing)**: Task 11 covers all 12 manual verification items; Task 15 covers the consumer-repo acceptance. ✅
- **Section 6 (breaking changes)**: Documented in the v2.0.0 tag message (Task 11 Step 6). ✅
- **Section 7 (acceptance)**: Items 1–7 verified in Task 11; items 8 (manual verification) and 9 (consumer-repo changes) covered by Task 15. ✅

No placeholders found. All code blocks are complete and self-contained. Types and function signatures are consistent across tasks (e.g., `_finalize_e2e_coverage` signature matches between definition in Task 7 Step 2 and call site in Task 7 Step 3).

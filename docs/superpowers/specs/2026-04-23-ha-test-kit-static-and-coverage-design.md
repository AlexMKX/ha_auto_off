# ha-test-kit: static analysis + coverage integration

**Status:** design approved, awaiting implementation plan.
**Scope:** upstream `ha-test-kit` (git submodule, https://github.com/AlexMKX/ha-test-kit).
**Version bump:** major (breaking defaults change for all consumers).

## Goals

Make every `autoqa` run produce:

1. **ruff lint + format check** on consumer integration code and on the
   harness itself. Fail-fast: errors block the rest of the run.
2. **vulture dead-code analysis** on the same scope. Warnings-only:
   always printed to stdout, never blocks the run.
3. **pytest-cov coverage** on consumer integration code, both in unit-mode
   (pytest-cov inside the autoqa container) and in e2e-mode (coverage
   instrumentation inside the Home Assistant container). Reports in
   terminal, HTML, XML, and raw `.coverage` formats, written to
   `.autoqa/coverage/{unit,e2e}/` on the host.

All three features are on by default and can be disabled independently
via environment variables.

## Non-goals

- mypy/pyright or other type checkers (ruff only covers lint + format).
- Automatic merging of unit and e2e coverage into a single combined
  report. Consumers who want this run `coverage combine` manually.
- Coverage thresholds (`--cov-fail-under`). Consumers can add via
  `AUTOQA_PYTEST_ARGS` if needed.
- CI-service-specific integrations (codecov, coveralls). The XML
  output is the standard Cobertura format consumed by any uploader.
- Self-tests for the harness itself. Verified manually via the
  consuming repository.
- Custom HA Docker image. Coverage.py is installed into a shared volume
  instead; official `ghcr.io/home-assistant/home-assistant` is used
  unchanged.
- Support for pre-major consumer versions. This is a major version bump;
  consumers who don't want the new behavior pin the prior tag.

## Architecture overview

`ha-test-kit` is a git submodule shipping a Docker-first test harness.
The harness container (`autoqa-unit` image, built from
`Dockerfile.autoqa`) runs `python -m ha_test_kit.autoqa`, which
dispatches on `AUTOQA_MODE=unit|seed|e2e`.

The consuming repository mounts itself read-only at `/work`. For e2e,
an additional container `homeassistant` (official HA image) runs in
parallel with a shared `/ha_config` volume.

### Flow per mode

```
unit-mode:
  static checks (ruff fail-fast → vulture warn) → install component
  requirements → pytest with --cov=custom_components → coverage reports
  to /work/.autoqa/coverage/unit/.

seed-mode (prepares /ha_config for e2e):
  seed HA configuration.yaml → discover ha_packages/ → copy .storage →
  bootstrap coverage (pip install --target=/ha_config/.coverage_pkg
  coverage, write .coveragerc and sitecustomize.py) → touch .autoqa_ready.

e2e-mode:
  static checks → install component requirements → wait for HA ready →
  onboarding → provision LLAT → pytest with docker_e2e marker →
  request HA shutdown via core.stop API → wait for .coverage.*
  fragments on /ha_config → coverage combine → reports to
  /work/.autoqa/coverage/e2e/.

e2e HA runtime (independent container):
  Python start → site loads sitecustomize.py from /config/.coverage_pkg
  → coverage.process_startup() reads COVERAGE_PROCESS_START →
  coverage tracing active for /config/custom_components → on SIGTERM,
  atexit hook writes .coverage.<host>.<pid>.<rand> to /ha_config/coverage-data/.
```

### Environment flags

| Variable | Default | Effect when `false` |
|---|---|---|
| `AUTOQA_LINT` | `true` | Skips ruff check + ruff format check. |
| `AUTOQA_VULTURE` | `true` | Skips vulture. |
| `AUTOQA_COVERAGE` | `true` | Skips coverage in unit and e2e. seed-mode skips coverage bootstrap. |

Each variable is parsed by the existing `_env_bool` helper (accepts
`1/true/yes/y/on`).

---

## Section 1: Static analysis (ruff + vulture)

### 1.1 Components

**`ha-test-kit/Dockerfile.autoqa`** — extend the existing `pip install`
line with:

- `ruff==0.6.*` (major-pinned)
- `vulture==2.14`

No new `apt-get` packages. Both tools are pure-Python wheels.

**`ha-test-kit/pyproject.toml`** — add three new sections: `[tool.ruff]`,
`[tool.ruff.lint]`, `[tool.ruff.format]`, `[tool.vulture]`. Detailed
contents in 1.2 and 1.3.

**`ha-test-kit/ha_test_kit/vulture_whitelist.py`** — new file. Copied
into the image via the existing `COPY ha_test_kit /opt/ha_test_kit/ha_test_kit`
line in the Dockerfile; no Dockerfile change needed.

**`ha-test-kit/ha_test_kit/autoqa.py`** — new function
`_run_static_checks(work_dir)` and one call site at the top of `main()`
for `mode in ("unit", "e2e")`.

### 1.2 ruff config

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
    "E501",   # line-too-long handled by formatter
]

[tool.ruff.lint.per-file-ignores]
"**/tests/**" = ["F401", "F811"]
"custom_components/**" = ["B008"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
docstring-code-format = true
```

Rule set rationale: broad enough that auto-fixable issues and
antipatterns are caught, narrow enough to be deterministic across
projects. `E501` is ignored because `ruff format` owns line width;
duplicating the rule produces spurious errors immediately after
formatting. `tests/F401,F811` covers pytest-fixture imports that ruff
sees as unused. `custom_components/B008` suppresses "mutable default
argument" warnings on HA service handlers that commonly take
`Callable = some_default()` constructor parameters.

### 1.3 vulture config

```toml
[tool.vulture]
exclude = ["*/tests/*"]
min_confidence = 60
ignore_decorators = ["@pytest.fixture", "@callback", "@staticmethod", "@property"]
sort_by_size = true
```

Paths are deliberately **not** set in the config. `_run_static_checks`
computes them at runtime based on what exists in `/work`, and passes
them as positional arguments to vulture. Hardcoding paths in the
generic config would break for consumers with different repo layouts.

**`ha-test-kit/ha_test_kit/vulture_whitelist.py`** — passed to vulture
as an additional input file. Each `name = None` statement creates a
textual reference that vulture counts as "used". The file documents
HA framework conventions that vulture cannot detect dynamically:

- Integration lifecycle: `async_setup_entry`, `async_unload_entry`,
  `async_migrate_entry`, `async_remove_config_entry_device`,
  `async_setup`.
- Config flow: `VERSION`, `async_step_user`, `async_step_init`,
  `async_step_import`, `async_get_options_flow`.
- Entity API: `device_info`, `extra_state_attributes`,
  `async_added_to_hass`, `async_will_remove_from_hass`,
  `async_set_value`, `native_value`.
- Entity `_attr_*` shortcuts: `_attr_name`, `_attr_unique_id`,
  `_attr_is_on`, `_attr_native_value`, `_attr_should_poll`,
  `_attr_device_class`, `_attr_icon`, `_attr_has_entity_name`.
- pydantic v2: `model_config`.

Project-specific symbols are **not** whitelisted. That's the consuming
project's responsibility: either use the symbol, remove it, or
`# noqa: vulture` locally (vulture 2.14 supports that comment syntax).

### 1.4 `_run_static_checks` flow

```python
def _run_static_checks(work_dir: Path) -> None:
    """Run ruff (fail-fast) and vulture (warnings-only) before tests.

    Both default to enabled; disable with AUTOQA_LINT=false and
    AUTOQA_VULTURE=false respectively.
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
                [sys.executable, "-m", "ruff", "check",
                 "--config", str(pyproject), *targets],
                cwd=work_dir,
            ).returncode
            if check_rc != 0:
                raise SystemExit(
                    f"[ruff] check failed with exit code {check_rc}"
                )

            _log("[ruff] format --check")
            fmt_rc = subprocess.run(
                [sys.executable, "-m", "ruff", "format", "--check",
                 "--config", str(pyproject), *targets],
                cwd=work_dir,
            ).returncode
            if fmt_rc != 0:
                raise SystemExit(
                    f"[ruff-format] check failed with exit code {fmt_rc}"
                )

    if _env_bool("AUTOQA_VULTURE", default=True) and targets:
        whitelist = Path("/opt/ha_test_kit/ha_test_kit/vulture_whitelist.py")
        result = subprocess.run(
            [sys.executable, "-m", "vulture",
             "--config", str(pyproject),
             *targets, str(whitelist)],
            cwd=work_dir,
            capture_output=True, text=True,
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

Call site in `main()`:

```python
def main() -> int:
    mode = os.environ.get("AUTOQA_MODE", "unit").strip().lower()
    work_dir = Path(os.environ.get("AUTOQA_WORK_DIR", "/work"))

    if mode in ("unit", "e2e"):
        _run_static_checks(work_dir)

    _install_component_requirements(work_dir)
    # ... unchanged below
```

### 1.5 Interaction with coverage

Static checks always run before coverage setup. If ruff fails, the
test run exits immediately: coverage is never collected. This is
correct behavior: coverage data from code that doesn't pass basic
hygiene has little signal value.

If vulture produces findings, they are logged but do not influence
coverage or pytest. Dead symbols still receive coverage accounting
(they'll show as 0% if unreachable).

---

## Section 2: Unit-mode coverage

### 2.1 pytest flags

In `_run_pytest`, when `AUTOQA_COVERAGE=true` and `mode=unit`, prepend
to the pytest args:

```
--cov=custom_components
--cov-branch
--cov-report=term-missing
--cov-report=html:/work/.autoqa/coverage/unit/html
--cov-report=xml:/work/.autoqa/coverage/unit/coverage.xml
```

And set env `COVERAGE_FILE=/work/.autoqa/coverage/unit/.coverage`
before invoking pytest (so the raw file lands next to the reports).

### 2.2 Output locations

Host-visible after `./ha-test-kit/run_unit.sh`:

```
.autoqa/coverage/unit/
├── .coverage            (raw, consumable by coverage combine)
├── coverage.xml         (Cobertura format)
└── html/
    ├── index.html
    └── ...
```

Terminal summary is printed after the test session, inline with pytest
output. Missing-line numbers are included (`term-missing` form).

### 2.3 run_unit.sh change

Mount the host `.autoqa` directory so reports survive the container:

```bash
docker run --rm \
  -v "${PROJECT_ROOT}:/work:ro" \
  -v "${PROJECT_ROOT}/.autoqa:/work/.autoqa" \
  -e AUTOQA_MODE=unit \
  ...
```

The first mount is read-only (whole repo); the second is a writable
subpath specifically for coverage output. Overlapping mounts are a
standard Docker pattern; the writable mount takes precedence for its
subtree.

---

## Section 3: E2E-mode coverage

### 3.1 seed-phase bootstrap

In `_seed_ha_configuration`, after the existing steps, call a new
`_seed_coverage_bootstrap(ha_config_dir)` when `AUTOQA_COVERAGE=true`.

```python
def _seed_coverage_bootstrap(ha_config_dir: Path) -> None:
    """Install coverage.py into /ha_config and drop sitecustomize.py.

    Runs inside the seed container (which has pip and network access).
    The HA container then loads these artefacts through PYTHONPATH and
    COVERAGE_PROCESS_START without needing coverage pre-installed.
    """
    pkg_dir = ha_config_dir / ".coverage_pkg"
    data_dir = ha_config_dir / "coverage-data"
    coveragerc = ha_config_dir / ".coveragerc"
    sitecustomize = pkg_dir / "sitecustomize.py"

    pkg_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    # Pinned version to match the autoqa container (same major/minor).
    subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--no-input", "--no-cache-dir",
         "--target", str(pkg_dir),
         "coverage==7.6.*"],
        check=True,
    )

    coveragerc.write_text(_COVERAGERC_CONTENTS, encoding="utf-8")
    sitecustomize.write_text(_SITECUSTOMIZE_CONTENTS, encoding="utf-8")

    _log(f"Coverage bootstrap prepared at {pkg_dir}")
```

### 3.2 docker-compose.yml env

On the `homeassistant` service, add:

```yaml
    environment:
      - TZ=UTC
      - COVERAGE_PROCESS_START=/config/.coveragerc
      - PYTHONPATH=/config/.coverage_pkg
```

These env vars are set unconditionally. If `AUTOQA_COVERAGE=false`,
the seed phase doesn't create the `.coverage_pkg/` directory or the
`.coveragerc`, so `COVERAGE_PROCESS_START` points at a non-existent
file (coverage.py silently ignores that) and `PYTHONPATH` points at
an empty/non-existent directory (Python silently ignores that).

On the `autoqa` service, add a writable mount for the host reports:

```yaml
    volumes:
      - .:/work:ro
      - ha_config:/ha_config
      - ./.autoqa:/work/.autoqa   # new: writable coverage output
      - ./.autoqa/test_results:/test_results
      - ./.autoqa/screenshots:/screenshots
```

### 3.3 HA runtime: sitecustomize + COVERAGE_PROCESS_START

**`/ha_config/.coverage_pkg/sitecustomize.py`** (written by seed phase):

```python
"""Coverage bootstrap loaded by CPython via the site module.

Present in the HA container via PYTHONPATH=/config/.coverage_pkg.
Python imports sitecustomize automatically when it appears on sys.path.
coverage.process_startup() reads COVERAGE_PROCESS_START from env,
starts tracing, and registers an atexit hook to persist the .coverage
data file.
"""
import os

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage
    except ModuleNotFoundError:
        # Coverage package is absent from /config/.coverage_pkg. This is
        # the expected state when AUTOQA_COVERAGE=false: the seed phase
        # skipped bootstrapping, but the docker-compose env still points
        # COVERAGE_PROCESS_START at a non-existent file. Silent exit.
        pass
    else:
        # Any other failure inside coverage itself must surface; silent
        # empty .coverage files would hide a real bug.
        coverage.process_startup()
```

The file is named `sitecustomize.py` specifically (CPython's `site`
module looks for that exact name). It lives in
`/config/.coverage_pkg/` rather than at `/config/` root to avoid
colliding with any `sitecustomize.py` a consuming project might place
in their HA config directory. Coverage is disabled at runtime by the
seed phase not creating `.coverage_pkg/`; `sitecustomize.py` (which
*is* created by seed phase) handles the follow-on gracefully.

Wait — if the seed phase skipped bootstrap entirely, `sitecustomize.py`
itself does not exist, so `PYTHONPATH=/config/.coverage_pkg` points at
a non-existent directory and Python simply doesn't load anything. The
handler above covers a different case: a stale `.coverage_pkg/` without
`coverage/` underneath it (rare, but possible across harness version
upgrades in the same HA volume). Fail-closed on broken installs,
fail-open on clean opt-out.

### 3.4 SIGTERM coordination and finalize flow

The existing `run_e2e.sh` uses
`docker compose up --abort-on-container-exit --exit-code-from autoqa`.
Naively: autoqa runs pytest then exits, compose sends SIGTERM to the
HA container, HA writes `.coverage.*` fragments — but by that point
autoqa is dead and nothing runs `coverage combine`.

Solution: autoqa explicitly initiates HA shutdown through the HA API,
waits for coverage fragments to appear on the shared volume, then
runs `coverage combine` before exiting.

New function `_finalize_e2e_coverage` in `autoqa.py`:

```python
def _finalize_e2e_coverage(
    base_url: str,
    access_token: str,
    ha_config_dir: Path,
    out_dir: Path,
) -> None:
    """Stop HA gracefully, wait for .coverage.* fragments, combine them."""
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

    # Poll for at least one fragment with a 30s budget.
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
            "env is set on the homeassistant service and that "
            "/config/.coverage_pkg/sitecustomize.py is importable."
        )

    # Give HA a brief grace period to finish any late writes.
    time.sleep(2)

    for frag in data_dir.glob(".coverage.*"):
        shutil.copyfile(frag, out_dir / frag.name)

    _log(f"[coverage] Combining {len(fragments)} fragments into {out_dir}")
    subprocess.run(
        [sys.executable, "-m", "coverage", "combine"],
        cwd=out_dir,
        check=True,
        env={**os.environ, "COVERAGE_RCFILE": str(ha_config_dir / ".coveragerc")},
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "report", "--show-missing"],
        cwd=out_dir,
        check=True,
        env={**os.environ, "COVERAGE_RCFILE": str(ha_config_dir / ".coveragerc")},
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "html",
         "-d", str(out_dir / "html")],
        cwd=out_dir,
        check=True,
        env={**os.environ, "COVERAGE_RCFILE": str(ha_config_dir / ".coveragerc")},
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "xml",
         "-o", str(out_dir / "coverage.xml")],
        cwd=out_dir,
        check=True,
        env={**os.environ, "COVERAGE_RCFILE": str(ha_config_dir / ".coveragerc")},
    )
```

In `main()` for `mode=e2e`, after pytest. Note: `_finalize_e2e_coverage`
accepts any Bearer-valid token; we pass `llat` (the long-lived token
already provisioned for pytest) rather than the short-lived
`access_token` because e2e runs can exceed the access token's
lifetime:

```python
    pytest_rc = _run_pytest(mode=mode, extra_env=extra_env)

    if _env_bool("AUTOQA_COVERAGE", default=True):
        _finalize_e2e_coverage(
            base_url=base_url,
            access_token=llat,
            ha_config_dir=ha_config_dir,
            out_dir=Path("/work/.autoqa/coverage/e2e"),
        )

    return pytest_rc
```

If `_finalize_e2e_coverage` raises, `main()` exits via the unhandled
exception path (non-zero rc). See Section 4 for exit-code semantics
when pytest itself also failed.

### 3.5 `.coveragerc` contents

```ini
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
    \.\.\.

[paths]
source =
    /config/custom_components
    custom_components
```

Notes on each setting:

- `branch = True` enables branch coverage, the modern default for
  Python ≥ 3.12.
- `parallel = True` produces one file per process/thread, named
  `.coverage.<host>.<pid>.<rand>`. Required for `coverage combine`
  afterwards.
- `concurrency = thread,asyncio` covers both HA's executor threads
  and its asyncio loop. Without these, the fraction of code running
  in those contexts (a lot of HA code) is lost.
- `source = /config/custom_components` restricts measurement to the
  integration code. HA core and site-packages are excluded implicitly.
- `data_file` sets the base path for `.coverage.*` fragments.
- `[paths]` remap — in the HA container, files live at
  `/config/custom_components/<domain>/...`. On the host and in the
  autoqa container, they live at `custom_components/<domain>/...`.
  The remap lets `coverage combine` and the HTML/XML reporters unify
  the two views into paths relative to the consumer's repo root.

### 3.6 Output locations (e2e)

Host-visible after `./ha-test-kit/run_e2e.sh`:

```
.autoqa/coverage/e2e/
├── .coverage                          (merged)
├── .coverage.<host>.<pid>.1           (raw fragments, kept for debugging)
├── .coverage.<host>.<pid>.2
├── coverage.xml
└── html/
    ├── index.html
    └── ...
```

Terminal summary is printed to stdout during finalize.

Cross-mode merge (documented in README, not automated):

```bash
coverage combine .autoqa/coverage/unit/.coverage .autoqa/coverage/e2e/.coverage
coverage html -d .autoqa/coverage/combined/html
```

---

## Section 4: Error handling (fail-fast)

| Scenario | Behavior |
|---|---|
| `AUTOQA_COVERAGE=false` and `AUTOQA_LINT=false` and `AUTOQA_VULTURE=false` | Harness behaves as pre-major version (minus unrelated breaking changes). No coverage, no static checks. |
| ruff check finds violations | `SystemExit` with `[ruff] check failed …`. pytest does not run. |
| ruff format --check finds diffs | `SystemExit` with `[ruff-format] check failed …`. |
| vulture finds dead code | Printed to stdout with `[vulture]` prefix; exit code ignored; run continues. |
| seed-mode `pip install coverage` fails | `subprocess.run(..., check=True)` raises `CalledProcessError`; seed container exits non-zero; `run_e2e.sh` fails before HA starts. |
| HA `sitecustomize.py` raises ImportError | HA Python exits on startup; `--abort-on-container-exit` propagates; autoqa receives SIGTERM and exits non-zero. |
| Zero `.coverage.*` fragments after 30s | `_finalize_e2e_coverage` raises `RuntimeError` with diagnostic message. autoqa exits non-zero. |
| `coverage combine` fails | `CalledProcessError` propagates; autoqa exits non-zero. |
| HA doesn't respond to `core.stop` | `requests.RequestException` inside `_finalize_e2e_coverage`; wrapped into `RuntimeError`. |
| pytest failed, coverage finalize succeeded | Exit code = pytest rc (non-zero). Reports still written. |
| pytest failed, coverage finalize also failed | Exit code = coverage error (unhandled exception). pytest failures visible in stdout; coverage error wins because it signals infrastructure breakage, which blocks diagnosing the pytest failures. |

Rationale for "coverage wins on dual failure": coverage is on by
default and is an infrastructure invariant. If it's broken, every
other observation is suspect. Forcing fail-fast on coverage failure
makes infrastructure bugs loud. Consumers who disagree set
`AUTOQA_COVERAGE=false` when they're debugging pytest in isolation.

---

## Section 5: Testing strategy

The harness itself does not ship with automated tests. Adding them
would require running Docker Compose from within pytest — a nested
e2e-for-the-e2e-harness — which is mind-bendy and fragile.

Verification relies on the consuming repository. After implementation,
running in `ha_switch_auto_off` must produce:

**Unit mode (`./ha-test-kit/run_unit.sh`):**

1. `.autoqa/coverage/unit/.coverage` exists (non-empty).
2. `.autoqa/coverage/unit/coverage.xml` parses as valid XML.
3. `.autoqa/coverage/unit/html/index.html` exists.
4. Terminal summary shows non-zero coverage for at least
   `custom_components/auto_off/integration_manager.py` and
   `custom_components/auto_off/auto_off.py`.
5. Test run exits 0 when code is clean.
6. Test run exits non-zero when ruff finds violations (verify by
   temporarily introducing an unused import).
7. vulture output printed with `[vulture]` prefix; run still exits 0.

**E2E mode (`./ha-test-kit/run_e2e.sh`):**

8. Same artefacts exist under `.autoqa/coverage/e2e/`.
9. `.coverage.*` fragments exist alongside the combined file.
10. HTML report uses repo-relative paths (thanks to `[paths]` remap),
    not `/config/custom_components/...`.
11. HA container shuts down cleanly (no `docker kill` needed).

**Disabling:**

12. `AUTOQA_COVERAGE=false AUTOQA_LINT=false AUTOQA_VULTURE=false ./ha-test-kit/run_unit.sh`
    produces no `.autoqa/coverage/` directory and no ruff/vulture output.

---

## Section 6: Breaking changes & rollback

This is a **major** version bump for `ha-test-kit`. Breaking changes:

- **Default behavior changes**: static checks and coverage run on
  every test invocation. Consumers who don't want them must set the
  three env flags explicitly.
- **New pinned dependencies** in the autoqa image: `ruff==0.6.*`,
  `vulture==2.14`, `coverage==7.6.*`, `pytest-cov==5.0.*`. Consumers
  who built their own images from `Dockerfile.autoqa` need to rebuild.
- **`docker-compose.yml` adds env and a mount** to `homeassistant` and
  `autoqa` services. Consumers who vendored the compose file need to
  re-vendor or opt out.
- **`.autoqa/coverage/` directory is created** in the consuming repo
  root. Consumers must add `.autoqa/` to `.gitignore` (most already
  have it for `.autoqa/test_results/` and `.autoqa/screenshots/`).

No rollback migration path: consumers pin the old submodule commit
if they can't adopt the new defaults. CHANGELOG in the upstream
repository documents the change explicitly.

---

## Section 7: Acceptance criteria

Implementation is complete when all of the following are true:

1. `ha-test-kit/Dockerfile.autoqa` installs `ruff==0.6.*`,
   `vulture==2.14`, `coverage==7.6.*`, `pytest-cov==5.0.*`.
2. `ha-test-kit/pyproject.toml` contains the ruff and vulture config
   sections specified in 1.2 and 1.3.
3. `ha-test-kit/ha_test_kit/vulture_whitelist.py` exists with the HA
   API whitelist from 1.3.
4. `ha-test-kit/ha_test_kit/autoqa.py`:
   - New `_run_static_checks(work_dir)` function.
   - New `_seed_coverage_bootstrap(ha_config_dir)` function.
   - New `_finalize_e2e_coverage(...)` function.
   - `main()` invokes `_run_static_checks` for `unit` and `e2e`.
   - `main()` for `seed` calls `_seed_coverage_bootstrap` when
     `AUTOQA_COVERAGE=true`.
   - `main()` for `e2e` calls `_finalize_e2e_coverage` after pytest
     when `AUTOQA_COVERAGE=true`.
   - `_run_pytest` injects `--cov=...` flags and sets `COVERAGE_FILE`
     env for `mode=unit` when coverage is on.
5. `ha-test-kit/docker-compose.yml`:
   - `homeassistant` service has `COVERAGE_PROCESS_START` and
     `PYTHONPATH` envs.
   - `autoqa` service mounts `./.autoqa:/work/.autoqa`.
6. `ha-test-kit/run_unit.sh` mounts `./.autoqa:/work/.autoqa`.
7. `ha-test-kit/README.md` has a new "Static analysis" section and a
   new "Coverage" section with env-var reference.
8. Manual verification in the consuming repo (auto_off) passes all
   12 acceptance items from Section 5.
9. Consumer-repo side changes (gitignore, fixing ruff violations
   surfaced by the first run) are handled by a separate plan/commit
   in the consuming repo, not in the ha-test-kit repo.

Implementation is split across two repositories:

- **Upstream `ha-test-kit`** — all changes listed in acceptance items
  1–7. Commits land in `github.com/AlexMKX/ha-test-kit`. The major
  version tag is created after all 7 items ship.
- **Consuming `ha_switch_auto_off`** — changes needed to consume the
  new ha-test-kit version: update the submodule pointer, add
  `.autoqa/` to `.gitignore`, fix any ruff/format violations the
  first run surfaces. These are separate commits in the consuming
  repo, after the upstream tag is available.

The writing-plans step will decompose both into commit-sized tasks.
Cross-repo dependency: upstream must be tagged before the consuming
plan runs, since the consuming repo pins a specific upstream commit.

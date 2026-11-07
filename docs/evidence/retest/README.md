# Retest record — 7 November 2026

The application and tests were run from commit `a55dadc6df99da680dd341361caa3fd60043a58e`. The following documentation commit adds these captures, a renderer, a rewritten README and CI on `main`; it does not change application or test source. Source hashes from the application run remain in [tested-sources.json](../tested-sources.json). The display scripts have changed since that run.

The host ran Linux, Python 3.12.14 and Textual 8.2.8. Results are in [verification.json](../verification.json), [pytest.xml](../pytest.xml) and the logs in this directory. Repository paths in text logs are replaced with `$REPO`; terminal colour escapes are removed. No user corpus or signed-in provider was used.

## Core and independent installation

```sh
.venv/bin/python -m build --outdir .local-evidence/retest/dist
.venv/bin/python -m venv '.local-evidence/retest/installed release'
'.local-evidence/retest/installed release/bin/python' -m pip install \
  -c requirements-runtime.lock .local-evidence/retest/dist/*.whl textual==8.2.8
AWB_INSTALLED_CLI="$PWD/.local-evidence/retest/installed release/bin/awb" \
  .venv/bin/pytest -vv --junitxml=.local-evidence/retest/pytest.xml
```

Observed: 78 passed, no skips. The installed scenario creates a project, saves a prompt, proposes and independently reviews a synthetic outcome, then exports, backs up and restores it. [Build](build.log) · [Install](install.log) · [Tests](pytest.log) · [Artifact hashes](packages.json)

## Real local integrations

The paths below are this development environment's separate upstream installations; substitute your installed locations. No optional dependency is silently downloaded by these probes.

```sh
.venv/bin/python scripts/probe_local_integrations.py \
  --basic-memory ../comparison/venvs/basic/bin/bm \
  --docling-python ../comparison/venvs/docling/bin/python \
  --qmd-js ../comparison/sources/qmd/dist/cli/qmd.js \
  --output .local-evidence/retest/local-integrations.json
.venv/bin/python scripts/probe_lcm.py ../comparison/sources/lcm \
  --output .local-evidence/retest/lcm.json
.venv/bin/python scripts/soak.py --seconds 30 \
  --output .local-evidence/retest/intake-soak.json
```

Observed: Basic Memory register/write/search, Docling numeric/qualification preservation, QMD collection/search and LCM capture/recall/cross-project rejection all passed. LCM summarization was disabled. The intake-only soak saved 3,920 prompts through 98 application reopen cycles with no loss; it did not exercise workers or a GUI. [Integration results](local-integrations.json) · [LCM](lcm.json) · [Soak](intake-soak.json)

## Writing, routing and qualification

The actual `Integrations.writing` adapter ran Vale on `evals/clear.md` and `evals/verbose.md`, using `evals/.vale.ini`. Vale found zero and three alerts respectively. Both exit codes were zero because these are warnings. Promptfoo ran `evals/promptfooconfig.yaml` through the same adapter, using a temporary local configuration directory. Its deterministic provider returned one correct example and one seeded false claim: the expected process exit was 100, with one passed and one rejected fixture, no errors. Telemetry opt-outs were set by the adapter before child startup.

`evaluation.routing_cases` ran all ten entries from `evals/routing.json`. It scored 9/10; the ambiguous report/design miss is retained in [routing.json](routing.json). This is a policy demonstration, not a live model benchmark.

`awb --home TEMP_EMPTY_STATE qualify --output qualification.json` exited 3, as expected when target-machine attestations are absent. [Qualification output](qualification.json) is a negative control; it does not invalidate the separately recorded installed-package test or claim target qualification.

## Recreate the images

```sh
.venv/bin/python -m pip install -r requirements-evidence.lock
.venv/bin/python scripts/render_test_evidence.py
.venv/bin/python scripts/capture_tui.py
.venv/bin/python - <<'PY'
from pathlib import Path
import cairosvg
for width in (80, 120):
    path = Path(f'docs/assets/tui-{width}.svg')
    cairosvg.svg2png(url=str(path), write_to=str(path.with_suffix('.png')))
PY
```

The report renderer consumes the committed JUnit and verification JSON and checks their counts. It maps every case to an image, records hashes and writes the searchable [case inventory](../TEST_CASES.md). The app captures come from Textual's headless test driver at two terminal sizes and show synthetic demonstration content. DejaVu Sans Mono is used locally; no remote font is fetched. Neither capture method constitutes an actual WezTerm desktop test.

All generated images were visually inspected. Current CI results are linked from the repository README; hosted Ubuntu/macOS runs remain separate from these local snapshots and retain their own JUnit artifacts.

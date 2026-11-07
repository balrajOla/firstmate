"""Bounded durable-intake soak. Explicitly not a live-agent/full-system soak."""

import argparse, concurrent.futures, json, tempfile, time
from pathlib import Path
from assistant_workbench.app import Application

p = argparse.ArgumentParser()
p.add_argument("--seconds", type=int, default=30)
p.add_argument("--output", required=True)
a = p.parse_args()
if not 1 <= a.seconds <= 259200:
    raise SystemExit("Use 1 second to 72 hours")
root = Path(tempfile.mkdtemp(prefix="awb-soak-"))
app = Application(root / "state")
project = app.store.add_project("Soak")
started = time.monotonic()
submitted = 0
cycles = 0
while time.monotonic() - started < a.seconds:
    offset = submitted
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        list(
            pool.map(
                lambda i: app.store.submit(
                    "Synthetic soak request " + str(i), "soak-" + str(i), project["id"]
                ),
                range(offset, offset + 40),
            )
        )
    submitted += 40
    cycles += 1
    # A fresh connection/application must recover every acknowledged intent.
    app = Application(root / "state")
    if len(app.store.rows("SELECT id FROM prompts")) != submitted:
        raise AssertionError("Lost prompt")
    for req in app.store.rows("SELECT id FROM requests WHERE status='queued' LIMIT 40"):
        app.store.cancel(req["id"])
    time.sleep(0.05)
result = {
    "classification": "local durable-intake soak; no real worker/model/GUI",
    "seconds": round(time.monotonic() - started, 2),
    "submitted": submitted,
    "restart_cycles": cycles,
    "lost_prompts": 0,
    "passed": True,
}
Path(a.output).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))

"""Start a real pinned LCM daemon with summarization disabled; test the adapter."""

import argparse, json, os, subprocess, tempfile, time
from pathlib import Path
from assistant_workbench.app import Application
from assistant_workbench.integrations import Integrations
from assistant_workbench.util import canonical

p = argparse.ArgumentParser()
p.add_argument("source")
p.add_argument("--output", required=True)
args = p.parse_args()
root = Path(tempfile.mkdtemp(prefix="awb-lcm-probe-"))
source = Path(args.source).resolve()
ready = root / "ready.json"
script = root / "daemon.mjs"
imports = {
    n: (source / "dist/src" / path).as_uri()
    for n, path in [
        ("server", "daemon/server.js"),
        ("config", "daemon/config.js"),
        ("paths", "lcm-paths.js"),
        ("auth", "daemon/auth.js"),
    ]
}
script.write_text(
    """import {writeFileSync} from 'node:fs';
const {createDaemon}=await import(SERVER);
const {loadDaemonConfig}=await import(CONFIG);
const {createLcmPaths}=await import(PATHS);
const {ensureAuthToken}=await import(AUTH);
const paths=createLcmPaths(ROOT);ensureAuthToken(paths.tokenPath);
const config=loadDaemonConfig(ROOT,{daemon:{port:0},llm:{provider:'disabled'}});
const daemon=await createDaemon(config,{paths,tokenPath:paths.tokenPath});
writeFileSync(READY,JSON.stringify({port:daemon.address().port,tokenPath:paths.tokenPath}));
process.on('SIGTERM',async()=>{await daemon.stop();process.exit(0)});
""".replace(
        "SERVER", json.dumps(imports["server"])
    )
    .replace("CONFIG", json.dumps(imports["config"]))
    .replace("PATHS", json.dumps(imports["paths"]))
    .replace("AUTH", json.dumps(imports["auth"]))
    .replace("ROOT", json.dumps(str(root / "lcm")))
    .replace("READY", json.dumps(str(ready)))
)
log = open(root / "daemon.log", "w")
env = os.environ.copy()
env["LCM_HOME"] = str(root / "lcm")
proc = subprocess.Popen(["node", str(script)], env=env, stdout=log, stderr=log)
checks = []
try:
    for _ in range(100):
        if ready.exists() or proc.poll() is not None:
            break
        time.sleep(0.1)
    if not ready.exists():
        raise RuntimeError(
            "LCM did not become ready: " + (root / "daemon.log").read_text()[-2000:]
        )
    state = json.loads(ready.read_text())
    os.environ["AWB_LCM_PROBE_TOKEN"] = Path(state["tokenPath"]).read_text().strip()
    app = Application(root / "workbench")
    repo = root / "project"
    repo.mkdir()
    other = root / "other"
    other.mkdir()
    project = app.store.add_project("LCM probe", str(repo))
    project2 = app.store.add_project("Other probe", str(other))
    cfg = json.loads((app.home / "config.json").read_text())
    cfg["integrations"]["lcm"] = {
        "enabled": True,
        "auth": "local",
        "allow_write": True,
        "summary_provider": "disabled",
        "projects": {
            project["id"]: {"cwd": str(repo)},
            project2["id"]: {"cwd": str(other)},
        },
        "url": "http://127.0.0.1:" + str(state["port"]),
        "token_env": "AWB_LCM_PROBE_TOKEN",
    }
    (app.home / "config.json").write_text(canonical(cfg))
    integration = Integrations(app.store)
    result = integration.history(
        project["id"],
        "capture",
        session_id="awb-session-1",
        client="codex",
        messages=[
            {
                "role": "user",
                "content": "NEBULA-AWB-473 must preserve refunds as an exclusion.",
                "tokenCount": 20,
                "timestamp": "2026-11-07T12:00:00Z",
            }
        ],
    )
    checks.append({"check": "real_lcm_capture", "passed": not result.get("error")})
    found = integration.history(project["id"], "search", query="NEBULA-AWB-473")
    checks.append(
        {"check": "real_lcm_recall", "passed": "NEBULA-AWB-473" in json.dumps(found)}
    )
    found = integration.history(project2["id"], "search", query="NEBULA-AWB-473")
    checks.append(
        {
            "check": "real_lcm_cross_project_negative",
            "passed": "NEBULA-AWB-473" not in json.dumps(found),
        }
    )
except Exception as exc:
    checks.append({"check": "real_lcm_probe", "passed": False, "error": str(exc)})
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log.close()
report = {
    "classification": "real LCM daemon and adapter; summarizer disabled; synthetic input",
    "checks": checks,
}
Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
raise SystemExit(0 if checks and all(c["passed"] for c in checks) else 1)

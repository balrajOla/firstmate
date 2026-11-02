//! Narrow Firstmate command bridge. Never reinterpret pane text as process truth.
use crate::*;
use std::{
    io::Read,
    process::{Command, Stdio},
    thread,
    time::Instant,
};

pub fn version(name: &str) -> Result<String> {
    let directory = std::env::temp_dir().join(id("olympus-doctor"));
    private_dir(&directory)?;
    let result = (|| -> Result<String> {
        let path = directory.join("version");
        private_write(&path, b"")?;
        let file = fs::OpenOptions::new().append(true).open(&path)?;
        let mut child = Command::new(name)
            .arg("--version")
            .stdin(Stdio::null())
            .stdout(file.try_clone()?)
            .stderr(file)
            .spawn()?;
        let start = Instant::now();
        loop {
            if let Some(status) = child.try_wait()? {
                if !status.success() {
                    bail!("version command failed")
                };
                break;
            }
            if start.elapsed() > Duration::from_secs(3) || path.metadata()?.len() > 65536 {
                let _ = child.kill();
                let _ = child.wait();
                bail!("version command exceeded its bound")
            }
            thread::sleep(Duration::from_millis(20));
        }
        Ok(fs::read_to_string(path)?.trim().to_string())
    })();
    let _ = fs::remove_dir_all(directory);
    result
}
pub fn doctor(root: Option<&Path>) -> Value {
    let checks=["claude","codex","muse","pi","node","git","restic"].iter().map(|name|{
        match version(name) {Ok(v)=>json!({"component":name,"installed":true,"version":v,"lifecycle_verified":false}),Err(e)=>json!({"component":name,"installed":false,"reason":e.to_string(),"lifecycle_verified":false})}
    }).collect::<Vec<_>>();
    json!({"components":checks,"firstmate_root":root,"firstmate_scripts_present":root.is_some_and(|r|["fm-spawn.sh","fm-send.sh","fm-control.sh","fm-fleet-snapshot.sh"].iter().all(|f|r.join("bin").join(f).is_file())),"strict_process_isolation":false,"note":"Installed is not lifecycle-verified. Use an isolated live compatibility run before enabling a profile. A same-user unrestricted shell can stop same-user services."})
}

/// Only a fresh canonical observation for the exact runtime identity can renew contact.
/// Endpoint existence alone never counts as a living model or an accepted result.
pub fn apply_observation(db: &Connection, body: &Value) -> Result<usize> {
    if body["schema"] != "fm-fleet-snapshot.v1" {
        bail!("unsupported Firstmate snapshot schema")
    }
    let tasks = body["tasks"]
        .as_array()
        .context("snapshot task inventory missing")?;
    let mut refreshed = 0;
    for observed in tasks {
        let Some(runtime) = observed["id"].as_str() else {
            continue;
        };
        let candidates = rows(
            db,
            "SELECT * FROM attempts WHERE runtime_id=? AND state IN ('running','unknown','paused','recovering','review')",
            [runtime],
        )?;
        for a in candidates {
            let current = &observed["current_state"];
            let stamp = current["observed_at"]
                .as_str()
                .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
                .map(|d| d.timestamp());
            if current["freshness"] != "fresh"
                || stamp.is_none_or(|t| now() - t > 30 || t > now() + 5)
            {
                continue;
            }
            let generation = observed["spawn_gen"].as_str().unwrap_or("");
            if generation.is_empty() {
                continue;
            }
            let old = a["runtime_generation"].as_str().unwrap_or("");
            if !old.is_empty() && old != generation {
                incident(db,a["task"].as_str(),"runtime_generation","Firstmate runtime generation changed; reconcile ownership before using its output","operator")?;
                continue;
            }
            let endpoint = &observed["endpoint"];
            let endpoint_stamp = endpoint["observed_at"]
                .as_str()
                .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
                .map(|d| d.timestamp());
            let stopped:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM operations WHERE attempt=? AND verb='exit' AND state='confirmed')",[a["id"].as_str()],|r|r.get(0))?;
            if observed["remote"].is_null()
                && !old.is_empty()
                && endpoint["freshness"] == "fresh"
                && endpoint_stamp.is_some_and(|t| now() - t <= 30 && t <= now() + 5)
                && (endpoint["agent_alive"] == "dead" || stopped && endpoint["exists"] == false)
            {
                db.execute("INSERT INTO stop_evidence VALUES(?,?,?,?,?) ON CONFLICT(attempt) DO UPDATE SET observed=excluded.observed,evidence=excluded.evidence",params![a["id"].as_str(),runtime,generation,now(),observed.to_string()])?;
                db.execute("UPDATE operations SET state='confirmed',receipt='Exact runtime observed dead' WHERE attempt=? AND verb='exit' AND state='uncertain'",[a["id"].as_str()])?;
            } else {
                db.execute(
                    "DELETE FROM stop_evidence WHERE attempt=?",
                    [a["id"].as_str()],
                )?;
            }
            if current["state"] == "working"
                && matches!(a["state"].as_str(), Some("running" | "unknown"))
            {
                let workspace = observed["paths"]["worktree"].as_str().unwrap_or("");
                db.execute("UPDATE attempts SET heartbeat=?,state='running',runtime_generation=?,workspace=CASE WHEN workspace='' THEN ? ELSE workspace END WHERE id=?",params![now(),generation,workspace,a["id"].as_str()])?;
                db.execute(
                    "UPDATE tasks SET state='running',reason='' WHERE id=? AND state='unknown'",
                    [a["task"].as_str()],
                )?;
                refreshed += 1;
            }
        }
    }
    db.execute("INSERT INTO runtime_observations VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,observed=excluded.observed",params![body.to_string(),now()])?;
    Ok(refreshed)
}

pub fn observe(root: &Path, fm_home: &Path, logs: &Path) -> Result<Value> {
    let out = command(
        root,
        fm_home,
        "fm-fleet-snapshot.sh",
        &["--json".into()],
        logs,
        30,
    )?;
    if !out.success {
        bail!("Firstmate observation failed: {}", out.text)
    }
    let v: Value = serde_json::from_str(&out.text)?;
    if v["schema"] != "fm-fleet-snapshot.v1" {
        bail!("unsupported Firstmate snapshot version")
    };
    Ok(v)
}

pub fn perform(store: &mut Store, root: &Path, fm_home: &Path, operation: &str) -> Result<Value> {
    let op = row(
        &store.db,
        "SELECT * FROM operations WHERE id=?",
        [operation],
    )?;
    if op["state"] != "pending" {
        bail!("operation is not pending; replay is forbidden")
    }
    let req: Value = serde_json::from_str(op["body"].as_str().context("operation body")?)?;
    let tid = op["task"].as_str().unwrap();
    let t = row(&store.db, "SELECT * FROM tasks WHERE id=?", [tid])?;
    let a = row(
        &store.db,
        "SELECT * FROM attempts WHERE id=?",
        [op["attempt"].as_str()],
    )?;
    if a["generation"] != t["generation"] {
        bail!("operation attempt was superseded")
    }
    let p = row(
        &store.db,
        "SELECT * FROM profiles WHERE id=? AND verified=1",
        [required(&req, "profile")?],
    )?;
    let harness = p["harness"].as_str().unwrap();
    let model = p["model"].as_str().unwrap();
    if version(harness)? != p["version"].as_str().unwrap_or("") {
        bail!("installed harness version changed; rerun live verification before dispatch")
    }
    let runtime_id = if a["runtime_id"].as_str().unwrap_or("").is_empty() {
        format!("olympus-{}", a["id"].as_str().unwrap().to_lowercase())
    } else {
        a["runtime_id"].as_str().unwrap().to_string()
    };
    if !runtime_id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        bail!("invalid exact runtime identity")
    }
    // Commit uncertainty BEFORE any external effect. A daemon crash cannot cause replay.
    if store.db.execute("UPDATE operations SET state='uncertain',receipt='Execution began; reconcile runtime before replay' WHERE id=? AND state='pending'",[operation])?!=1{bail!("operation already claimed; replay is forbidden")};
    store.db.execute(
        "UPDATE attempts SET runtime_id=? WHERE id=?",
        params![runtime_id, a["id"].as_str()],
    )?;
    let verb = op["verb"].as_str().unwrap();
    let logs = store.home.join("bridge-logs");
    private_dir(&logs)?;
    let execute = (|| -> Result<CommandResult> {
        match verb {
            "launch" => {
                let project = row(
                    &store.db,
                    "SELECT * FROM projects WHERE id=?",
                    [t["project"].as_str()],
                )?;
                let repository = PathBuf::from(project["repository"].as_str().unwrap_or(""));
                if !repository.is_dir() {
                    bail!("project repository is not a local directory")
                }
                if fm_home.join("data").join(&runtime_id).exists() {
                    bail!("runtime identity already exists; reconcile it instead of relaunching")
                }
                let scaffold = command(
                    root,
                    fm_home,
                    "fm-brief.sh",
                    &[
                        runtime_id.clone(),
                        project["name"].as_str().unwrap().into(),
                        "--mode".into(),
                        "local-only".into(),
                    ],
                    &logs,
                    30,
                )?;
                if !scaffold.success {
                    bail!("brief creation failed: {}", scaffold.text)
                }
                let path = fm_home.join("data").join(&runtime_id).join("brief.md");
                let template = fs::read_to_string(&path)?;
                if template.matches("{TASK}").count() != 1
                    || template.matches("{FIRSTMATE_SPEC}").count() != 1
                {
                    bail!("Firstmate brief schema changed")
                }
                let agent = row(
                    &store.db,
                    "SELECT * FROM agents WHERE id=?",
                    [a["agent"].as_str()],
                )?;
                let credential = store
                    .home
                    .join("credentials")
                    .join(format!("{}.token", agent["id"].as_str().unwrap()));
                let daemon = std::env::current_exe()?;
                let binary_dir = daemon.parent().context("binary directory")?;
                let executable = if binary_dir.join("olympus-real").is_file() {
                    binary_dir.join("olympus-real")
                } else {
                    binary_dir.join("olympus")
                };
                if !executable.is_file() {
                    bail!("worker broker executable is missing")
                }
                let reference = if binary_dir.join("docs/olympus-api.md").is_file() {
                    binary_dir.join("docs/olympus-api.md")
                } else {
                    root.join("docs/olympus-api.md")
                };
                let connection = json!({"binary":executable,"home":store.home,"credential_file":credential,"task":tid,"generation":t["generation"]});
                let spec=format!("Olympus task {tid}, requirement version {}, attempt generation {}. Role: {}. Read only this task scope. Preserve the workspace. Report artifacts, code revision and test evidence. Process exit is not task acceptance. Native fan-out is disabled unless every child has a separate Olympus admission reservation. Use the assigned broker credential, never operator credentials. Connection: {connection}. Submit JSON through `olympus --home HOME --token-file CREDENTIAL request -`. Start by reading task.get, acknowledge task messages via message.ack delivered/read/acted, send task.heartbeat every 15 seconds, report distinct task.progress checkpoints, and finish with task.result including body and code_revision. Read docs/olympus-api.md in the Firstmate code root for the exact action contract. Do not reveal credential contents.",t["version"],t["generation"],agent["role"]);
                let context = crate::portfolio::context(
                    &store.db,
                    &Principal::operator(),
                    project["id"].as_str().unwrap(),
                )?;
                let role = crate::actions::role_text(agent["role"].as_str().unwrap());
                let spec=format!("{spec}\nExact installed action reference: {}. Invoke the absolute binary in Connection when olympus is not on PATH.\nRole contract: {role}\nProject context (retrieve referenced full records as needed): {context}\nRead task.get attempts and preserve/reconcile any earlier workspace before continuing recovered work. For a review job use review.submit, not task.result. For independent bounded help use child.reserve, then message.send; the coordinator launches admitted children when project automation is enabled. Never bypass child admission. Stop repeating an unchanged failure; report task.block with an owner and a future next_check.",reference.display());
                private_write(
                    &path,
                    template
                        .replace("{TASK}", t["requirements"].as_str().unwrap())
                        .replace("{FIRSTMATE_SPEC}", &spec)
                        .as_bytes(),
                )?;
                command(
                    root,
                    fm_home,
                    "fm-spawn.sh",
                    &[
                        runtime_id.clone(),
                        repository.to_string_lossy().into(),
                        "--mode".into(),
                        "local-only".into(),
                        "--yolo".into(),
                        "off".into(),
                        "--harness".into(),
                        harness.into(),
                        "--model".into(),
                        model.into(),
                    ],
                    &logs,
                    60,
                )
            }
            "message" => command(
                root,
                fm_home,
                "fm-send.sh",
                &[
                    runtime_id.clone(),
                    "--fire-and-forget".into(),
                    operation.into(),
                    required(&req, "body")?,
                ],
                &logs,
                30,
            ),
            "interrupt" | "exit" => command(
                root,
                fm_home,
                "fm-control.sh",
                &[runtime_id.clone(), verb.into()],
                &logs,
                30,
            ),
            "relaunch" => command(
                root,
                fm_home,
                "fm-control.sh",
                &[
                    runtime_id.clone(),
                    "relaunch".into(),
                    "--harness".into(),
                    harness.into(),
                    "--model".into(),
                    model.into(),
                    "--note".into(),
                    required(&req, "body")?,
                ],
                &logs,
                60,
            ),
            _ => bail!("unsupported lifecycle verb"),
        }
    })();
    let (state, text) = match execute {
        Ok(o) => (if o.success { "confirmed" } else { "uncertain" }, o.text),
        Err(e) => ("uncertain", e.to_string()),
    };
    store.db.execute(
        "UPDATE operations SET state=?,receipt=? WHERE id=?",
        params![state, text, operation],
    )?;
    if state == "confirmed" {
        store.db.execute(
            "UPDATE attempts SET runtime_id=? WHERE id=?",
            params![runtime_id, a["id"].as_str()],
        )?;
    } else {
        incident(
            &store.db,
            Some(tid),
            "uncertain_effect",
            "Runtime operation needs reconciliation; automatic replay is disabled",
            "operator",
        )?;
    }
    Ok(
        json!({"operation":operation,"state":state,"runtime_id":runtime_id,"receipt":text,"task_accepted":false}),
    )
}

struct CommandResult {
    success: bool,
    text: String,
}
fn command(
    root: &Path,
    home: &Path,
    script: &str,
    args: &[String],
    logs: &Path,
    timeout: u64,
) -> Result<CommandResult> {
    if !root.join("bin").join(script).is_file() {
        bail!("Firstmate script missing: {script}")
    }
    private_dir(logs)?;
    let path = logs.join(format!("{}.log", id("call")));
    private_write(&path, b"")?;
    let file = fs::OpenOptions::new().append(true).open(&path)?;
    let mut child = Command::new("bash")
        .arg(root.join("bin").join(script))
        .args(args)
        .current_dir(root)
        .env("FM_HOME", home)
        .stdin(Stdio::null())
        .stdout(file.try_clone()?)
        .stderr(file)
        .spawn()?;
    let start = Instant::now();
    let success = loop {
        if let Some(s) = child.try_wait()? {
            break s.success();
        }
        if start.elapsed() > Duration::from_secs(timeout) || path.metadata()?.len() > 2_097_152 {
            let _ = child.kill();
            let _ = child.wait();
            break false;
        }
        thread::sleep(Duration::from_millis(50));
    };
    let mut text = String::new();
    fs::File::open(&path)?
        .take(2_097_152)
        .read_to_string(&mut text)?;
    Ok(CommandResult { success, text })
}

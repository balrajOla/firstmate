use crate::*;

pub fn preview(db: &Connection, actor: &Principal, req: &Value) -> Result<Value> {
    let text = required(req, "text")?;
    let lower = text.to_lowercase();
    let explicit = optional(req, "project");
    let candidates = rows(
        db,
        "SELECT id,name,aliases,charter FROM projects WHERE state!='completed' AND (? OR id=?)",
        params![actor.global(), actor.project],
    )?
    .into_iter()
    .filter(|p| {
        if !explicit.is_empty() {
            return p["id"] == explicit;
        }
        let names: Vec<String> =
            serde_json::from_str(p["aliases"].as_str().unwrap_or("[]")).unwrap_or_default();
        std::iter::once(p["name"].as_str().unwrap_or("").to_string())
            .chain(names)
            .any(|n| n.len() >= 3 && lower.contains(&n.to_lowercase()))
    })
    .collect::<Vec<_>>();
    let related = if candidates.len() == 1 {
        rows(db,"SELECT id,title,version,state FROM tasks WHERE project=? AND state NOT IN ('accepted','cancelled') ORDER BY updated DESC LIMIT 12",[candidates[0]["id"].as_str()])?
    } else {
        vec![]
    };
    Ok(
        json!({"input":text,"route":if candidates.len()==1{"existing_project"}else if candidates.is_empty(){"needs_project_or_new_charter"}else{"needs_clarification"},"candidates":candidates,"related_tasks":related,"mutated":false,"next":"Choose a task amendment, new task, or new project charter explicitly. Similarity alone never rewrites existing requirements."}),
    )
}

pub fn mutate(db: &Connection, actor: &Principal, r: &Value) -> Result<Value> {
    let action = required(r, "action")?;
    match action.as_str() {
        "task.create" => {
            require_manager(actor)?;
            let project = required(r, "project")?;
            actor.scope(&project)?;
            row(
                db,
                "SELECT id FROM projects WHERE id=? AND state='active'",
                [&project],
            )?;
            let tid = id("T");
            let title = required(r, "title")?;
            let body = required(r, "requirements")?;
            let role = optional(r, "role");
            db.execute("INSERT INTO tasks(id,project,title,requirements,role,priority,created,updated) VALUES(?,?,?,?,?,?,?,?)",params![tid,project,title,body,if role.is_empty(){"engineer"}else{&role},number(r,"priority",1).clamp(0,5),now(),now()])?;
            db.execute(
                "INSERT INTO requirements VALUES(?,1,?,?)",
                params![tid, body, now()],
            )?;
            Ok(json!({"task":tid,"version":1,"state":"ready"}))
        }
        "task.amend" => {
            require_manager(actor)?;
            let tid = required(r, "task")?;
            let t = task(db, actor, &tid)?;
            if matches!(t["state"].as_str(), Some("accepted" | "cancelled")) {
                bail!("create a follow-up task for completed work")
            }
            if number(r, "expected_version", -1) != t["version"].as_i64().unwrap() {
                bail!("requirement version conflict")
            }
            let body = required(r, "requirements")?;
            let version = t["version"].as_i64().unwrap() + 1;
            let state = if t["generation"].as_i64().unwrap() > 0 {
                "blocked"
            } else {
                "ready"
            };
            db.execute("UPDATE tasks SET requirements=?,version=?,state=?,reason='Requirements changed; owner must acknowledge at a safe checkpoint',updated=? WHERE id=?",params![body,version,state,now(),tid])?;
            db.execute(
                "INSERT INTO requirements VALUES(?,?,?,?)",
                params![tid, version, body, now()],
            )?;
            if state == "blocked" {
                incident(
                    db,
                    Some(&tid),
                    "requirements_changed",
                    "Acknowledge current requirements at a safe checkpoint",
                    "captain",
                )?;
            }
            Ok(json!({"task":tid,"version":version,"state":state}))
        }
        "task.ack_version" => {
            let tid = required(r, "task")?;
            let t = task(db, actor, &tid)?;
            let a = row(
                db,
                "SELECT * FROM attempts WHERE task=? AND generation=?",
                params![tid, t["generation"].as_i64()],
            )?;
            if !actor.is_operator() && a["agent"] != actor.id {
                bail!("only the attempt owner may acknowledge")
            }
            if !matches!(a["state"].as_str(), Some("running" | "review" | "paused")) {
                bail!("reconcile the runtime before acknowledging")
            }
            if number(r, "version", -1) != t["version"].as_i64().unwrap() {
                bail!("requirement version conflict")
            }
            required(r, "checkpoint")?;
            db.execute("UPDATE attempts SET version=?,state='running',heartbeat=?,progress_at=? WHERE id=?",params![t["version"].as_i64(),now(),now(),a["id"].as_str()])?;
            db.execute(
                "UPDATE tasks SET state='running',reason='',updated=? WHERE id=?",
                params![now(), tid],
            )?;
            db.execute("UPDATE incidents SET state='resolved',resolution='Current version acknowledged' WHERE task=? AND kind='requirements_changed' AND state='open'",[&tid])?;
            Ok(json!({"task":tid,"version":t["version"],"acknowledged":true}))
        }
        "task.link" => {
            require_manager(actor)?;
            let tid = required(r, "task")?;
            let dep = required(r, "depends_on")?;
            let t = task(db, actor, &tid)?;
            task(db, actor, &dep)?;
            if t["state"] != "ready" {
                bail!("dependencies can only be added before an attempt starts")
            }
            let cycle:bool=db.query_row("WITH RECURSIVE chain(x) AS (SELECT ? UNION SELECT d.depends_on FROM dependencies d JOIN chain c ON d.task=c.x) SELECT EXISTS(SELECT 1 FROM chain WHERE x=?)",params![dep,tid],|r|r.get(0))?;
            if cycle {
                bail!("dependency cycle rejected")
            }
            db.execute(
                "INSERT OR IGNORE INTO dependencies VALUES(?,?)",
                params![tid, dep],
            )?;
            Ok(json!({"linked":true}))
        }
        "task.claim" => claim(db, actor, r),
        "task.heartbeat" => {
            let (t, a) = current_attempt(db, actor, r)?;
            db.execute(
                "UPDATE attempts SET heartbeat=? WHERE id=?",
                params![now(), a["id"].as_str()],
            )?;
            if t["state"] == "unknown" && a["state"] == "unknown" {
                db.execute(
                    "UPDATE attempts SET state='running' WHERE id=?",
                    [a["id"].as_str()],
                )?;
                db.execute(
                    "UPDATE tasks SET state='running',reason='' WHERE id=?",
                    [t["id"].as_str()],
                )?;
                db.execute("UPDATE incidents SET state='resolved',resolution='Current authenticated owner reconnected' WHERE task=? AND kind='observation_stale' AND state='open'",[t["id"].as_str()])?;
            }
            Ok(json!({"contact":now(),"useful_progress_unchanged":true}))
        }
        "task.progress" => {
            let (t, a) = current_attempt(db, actor, r)?;
            if t["state"] != "running" || a["state"] != "running" {
                bail!("progress requires a running attempt")
            }
            let fingerprint = required(r, "fingerprint")?;
            let same = a["fingerprint"] == fingerprint;
            let repeats = if same {
                a["repeats"].as_i64().unwrap() + 1
            } else {
                0
            };
            // Heartbeats are never counted as useful progress.
            let substantive = r["checkpoint"]
                .as_str()
                .is_some_and(|s| !s.trim().is_empty());
            db.execute(
                "UPDATE attempts SET heartbeat=?,progress_at=?,fingerprint=?,repeats=? WHERE id=?",
                params![
                    now(),
                    if substantive && !same {
                        now()
                    } else {
                        a["progress_at"].as_i64().unwrap()
                    },
                    fingerprint,
                    repeats,
                    a["id"].as_str()
                ],
            )?;
            if repeats >= 3 {
                db.execute("UPDATE tasks SET state='blocked',reason='Repeated attempt without changed evidence; one bounded replan required',updated=? WHERE id=?",params![now(),t["id"].as_str()])?;
                db.execute(
                    "UPDATE attempts SET state='paused' WHERE id=?",
                    [a["id"].as_str()],
                )?;
                incident(db,t["id"].as_str(),"no_progress","Three repeated fingerprints; inspect and change the hypothesis before resuming","captain")?;
            }
            Ok(json!({"heartbeat":now(),"loop_detected":repeats>=3,"repeats":repeats}))
        }
        "task.result" => {
            let (t, a) = current_attempt(db, actor, r)?;
            if t["state"] != "running" {
                bail!("candidate result requires a running current task")
            }
            let pending:i64=db.query_row("SELECT COUNT(*) FROM dependencies d JOIN tasks child ON child.id=d.depends_on WHERE d.task=? AND child.state!='accepted'",[t["id"].as_str()],|r|r.get(0))?;
            if pending > 0 {
                bail!("dependent or child work is not yet accepted")
            }
            let body = required(r, "body")?;
            let code = required(r, "code_revision")?;
            let artifact = id("A");
            let digest = hash(body.as_bytes());
            db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)",
                params![
                    artifact,
                    t["id"].as_str(),
                    a["id"].as_str(),
                    t["version"].as_i64(),
                    digest,
                    body,
                    code,
                    now()
                ],
            )?;
            db.execute(
                "UPDATE tasks SET state='review',updated=? WHERE id=?",
                params![now(), t["id"].as_str()],
            )?;
            db.execute(
                "UPDATE attempts SET state='review' WHERE id=?",
                [a["id"].as_str()],
            )?;
            response(
                db,
                t["project"].as_str().unwrap(),
                t["id"].as_str().unwrap(),
                &body,
            )?;
            Ok(json!({"artifact":artifact,"sha":digest,"state":"review","accepted":false}))
        }
        "task.review" => {
            if !actor.is_operator() && !matches!(actor.role.as_str(), "quality" | "reviewer") {
                bail!("independent reviewer role required")
            }
            let artifact = required(r, "artifact")?;
            let art=row(db,"SELECT a.*,p.agent AS producer FROM artifacts a JOIN attempts p ON a.attempt=p.id WHERE a.id=?",[&artifact])?;
            let t = task(db, actor, art["task"].as_str().unwrap())?;
            let reviewer = if actor.is_operator() {
                required(r, "reviewer")?
            } else {
                actor.id.clone()
            };
            let reviewer_row=row(db,"SELECT * FROM agents WHERE id=? AND role IN ('reviewer','quality') AND state!='retired'",[&reviewer])?;
            if reviewer_row["project"] != t["project"] {
                bail!("reviewer project mismatch")
            }
            if reviewer == art["producer"].as_str().unwrap() {
                bail!("self-review is forbidden")
            }
            if t["version"] != art["version"] || t["state"] != "review" {
                bail!("artifact is stale or task is not in review")
            }
            let current = row(
                db,
                "SELECT id FROM attempts WHERE task=? AND generation=? AND state='review'",
                params![t["id"].as_str(), t["generation"].as_i64()],
            )?;
            if current["id"] != art["attempt"] {
                bail!("artifact belongs to a superseded attempt")
            }
            if required(r, "code_revision")? != art["code_revision"].as_str().unwrap() {
                bail!("review code revision mismatch")
            }
            let evidence = required(r, "evidence")?;
            let passed = r["passed"].as_bool().context("passed must be a boolean")?;
            db.execute(
                "INSERT INTO reviews VALUES(?,?,?,?,?,?)",
                params![id("V"), artifact, reviewer, passed, evidence, now()],
            )?;
            let state = if passed { "accepted" } else { "running" };
            db.execute(
                "UPDATE tasks SET state=?,completed=?,reason=?,updated=? WHERE id=?",
                params![
                    state,
                    if passed { Some(now()) } else { None },
                    if passed { "" } else { &evidence },
                    now(),
                    t["id"].as_str()
                ],
            )?;
            db.execute(
                "UPDATE attempts SET state=?,heartbeat=? WHERE id=?",
                params![
                    if passed { "accepted" } else { "running" },
                    now(),
                    art["attempt"].as_str()
                ],
            )?;
            if passed {
                db.execute("UPDATE incidents SET state='resolved',resolution='Current acceptance contract satisfied' WHERE task=? AND state='open'",[t["id"].as_str()])?;
            }
            let body = format!(
                "{}: {}\n\nEvidence: {}\nArtifact: {}",
                if passed {
                    "Accepted"
                } else {
                    "Rework required"
                },
                t["title"].as_str().unwrap(),
                evidence,
                artifact
            );
            response(
                db,
                t["project"].as_str().unwrap(),
                t["id"].as_str().unwrap(),
                &body,
            )?;
            if passed {
                db.execute(
                    "UPDATE tasks SET completed_seq=? WHERE id=?",
                    params![db.last_insert_rowid(), t["id"].as_str()],
                )?;
            }
            Ok(json!({"task":t["id"],"state":state}))
        }
        "task.block" => {
            let (t, _) = current_attempt(db, actor, r)?;
            let reason = required(r, "reason")?;
            let owner = required(r, "owner")?;
            let next = number(r, "next_check", 0);
            if next <= now() {
                bail!("blocked work requires a future next_check")
            }
            db.execute(
                "UPDATE tasks SET state='blocked',reason=?,next_check=?,updated=? WHERE id=?",
                params![reason, next, now(), t["id"].as_str()],
            )?;
            incident(db, t["id"].as_str(), "blocked", &reason, &owner)?;
            Ok(json!({"state":"blocked","next_check":next}))
        }
        "task.pause" | "task.resume" | "task.cancel" => {
            require_manager(actor)?;
            let tid = required(r, "task")?;
            let t = task(db, actor, &tid)?;
            if matches!(t["state"].as_str(), Some("accepted" | "cancelled")) {
                bail!("task is already terminal")
            }
            if action == "task.cancel" {
                require_operator(actor)?;
                if t["generation"].as_i64().unwrap() > 0 && r["confirmed_stopped"] != true {
                    bail!("confirm the runtime stopped and reconcile effects before cancellation")
                }
                required(r, "reason")?;
                db.execute("UPDATE attempts SET state='fenced' WHERE task=? AND state IN ('running','unknown','paused','review','recovering')",[&tid])?;
                db.execute(
                    "UPDATE tasks SET state='cancelled',reason=?,updated=? WHERE id=?",
                    params![required(r, "reason")?, now(), tid],
                )?;
                return Ok(json!({"state":"cancelled","work_preserved":true}));
            }
            if action == "task.pause" {
                db.execute(
                    "UPDATE tasks SET state='paused',reason=?,updated=? WHERE id=?",
                    params![required(r, "reason")?, now(), tid],
                )?;
                db.execute(
                    "UPDATE attempts SET state='paused' WHERE task=? AND state='running'",
                    [&tid],
                )?;
                return Ok(
                    json!({"state":"paused","runtime_interruption_confirmed":false,"reservation_retained":true}),
                );
            }
            if t["state"] == "unknown" {
                bail!("unknown runtime requires explicit reconciliation")
            }
            required(r, "reason")?;
            let looped:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM incidents WHERE task=? AND kind='no_progress' AND state='open')",[&tid],|r|r.get(0))?;
            if looped {
                if t["replans"].as_i64().unwrap() >= 1 {
                    bail!("bounded replan exhausted; operator recovery or cancellation required")
                }
                db.execute("UPDATE tasks SET replans=replans+1 WHERE id=?", [&tid])?;
                db.execute("UPDATE incidents SET state='resolved',resolution=? WHERE task=? AND kind='no_progress' AND state='open'",params![required(r,"reason")?,tid])?;
            }
            let current = rows(
                db,
                "SELECT id FROM attempts WHERE task=? AND state IN ('paused','running')",
                [&tid],
            )?;
            let state = if current.is_empty() {
                "ready"
            } else {
                "running"
            };
            db.execute(
                "UPDATE tasks SET state=?,reason='',updated=? WHERE id=?",
                params![state, now(), tid],
            )?;
            db.execute("UPDATE attempts SET state='running',repeats=0,heartbeat=? WHERE task=? AND state='paused'",params![now(),tid])?;
            Ok(json!({"state":state}))
        }
        "task.recover" => {
            require_operator(actor)?;
            let tid = required(r, "task")?;
            let t = task(db, actor, &tid)?;
            if matches!(
                t["state"].as_str(),
                Some("accepted" | "cancelled" | "ready")
            ) {
                bail!("task does not need recovery")
            }
            if r["confirmed_stopped"] != true {
                bail!("unknown is not dead: confirm runtime fencing before replacement")
            }
            let evidence = required(r, "evidence")?;
            if t["recoveries"].as_i64().unwrap() >= 2 {
                incident(
                    db,
                    Some(&tid),
                    "recovery_exhausted",
                    "Two recovery attempts used; operator decision required",
                    "operator",
                )?;
                db.execute("UPDATE tasks SET state='blocked',reason='Recovery budget exhausted' WHERE id=?",[&tid])?;
                return Ok(json!({"state":"blocked","reason":"recovery_budget_exhausted"}));
            }
            db.execute("UPDATE attempts SET state='fenced' WHERE task=? AND state IN ('running','unknown','paused','review','recovering')",[&tid])?;
            db.execute("UPDATE tasks SET state='ready',owner=NULL,recoveries=recoveries+1,reason=?,updated=? WHERE id=?",params![evidence,now(),tid])?;
            db.execute(
                "UPDATE incidents SET state='resolved',resolution=? WHERE task=? AND state='open'",
                params![evidence, tid],
            )?;
            Ok(
                json!({"state":"ready","workspace_preserved":true,"next_generation":t["generation"].as_i64().unwrap()+1}),
            )
        }
        "incident.resolve" => {
            require_manager(actor)?;
            let iid = required(r, "incident")?;
            let i = row(db, "SELECT * FROM incidents WHERE id=?", [&iid])?;
            if let Some(t) = i["task"].as_str() {
                task(db, actor, t)?;
            } else {
                require_operator(actor)?;
            }
            if i["owner"] == "operator" {
                require_operator(actor)?;
            }
            db.execute(
                "UPDATE incidents SET state='resolved',resolution=? WHERE id=?",
                params![required(r, "resolution")?, iid],
            )?;
            Ok(json!({"resolved":true,"task_state_unchanged":true}))
        }
        _ => bail!("unknown task action"),
    }
}

pub(super) fn claim(db: &Connection, actor: &Principal, r: &Value) -> Result<Value> {
    let tid = required(r, "task")?;
    let t = task(db, actor, &tid)?;
    if t["state"] != "ready" {
        bail!("task is not ready")
    }
    row(
        db,
        "SELECT id FROM projects WHERE id=? AND state='active'",
        [t["project"].as_str()],
    )?;
    let agent = if actor.is_operator() || actor.manages() {
        required(r, "agent")?
    } else {
        actor.id.clone()
    };
    let a = row(
        db,
        "SELECT * FROM agents WHERE id=? AND state!='retired'",
        [&agent],
    )?;
    if a["project"] != t["project"] {
        bail!("agent project mismatch")
    }
    if a["role"] != t["role"] {
        bail!("task requires a different role package")
    }
    let deps:i64=db.query_row("SELECT COUNT(*) FROM dependencies d JOIN tasks t ON t.id=d.depends_on WHERE d.task=? AND t.state!='accepted'",[&tid],|r|r.get(0))?;
    if deps > 0 {
        bail!("waiting for accepted dependencies")
    }
    let running:i64=db.query_row("SELECT COALESCE(SUM(1+reserved_children),0) FROM attempts WHERE state IN ('running','unknown','paused','review','recovering')",[],|r|r.get(0))?;
    let in_project:i64=db.query_row("SELECT COALESCE(SUM(1+a.reserved_children),0) FROM attempts a JOIN tasks t ON t.id=a.task WHERE t.project=? AND a.state IN ('running','unknown','paused','review','recovering')",[t["project"].as_str()],|r|r.get(0))?;
    let busy:i64=db.query_row("SELECT COUNT(*) FROM attempts WHERE agent=? AND state IN ('running','unknown','paused','review','recovering')",[&agent],|r|r.get(0))?;
    let global: i64 = db.query_row(
        "SELECT CAST(value AS INTEGER) FROM settings WHERE key='global_cap'",
        [],
        |r| r.get(0),
    )?;
    let local: i64 = db.query_row(
        "SELECT CAST(value AS INTEGER) FROM settings WHERE key='project_cap'",
        [],
        |r| r.get(0),
    )?;
    let reserve_children = number(r, "reserve_children", 0);
    if !(0..=8).contains(&reserve_children) {
        bail!("reserve_children must be 0..8")
    }
    if running + 1 + reserve_children > global
        || in_project + 1 + reserve_children > local
        || busy > 0
    {
        bail!("capacity unavailable; request remains queued")
    }
    let generation = t["generation"].as_i64().unwrap() + 1;
    let aid = id("RUN");
    let previous = rows(
        db,
        "SELECT workspace FROM attempts WHERE task=? ORDER BY generation DESC LIMIT 1",
        [&tid],
    )?;
    let workspace = if let Some(p) = previous.first() {
        p["workspace"].as_str().unwrap_or("").to_string()
    } else {
        optional(r, "workspace")
    };
    db.execute("INSERT INTO attempts(id,task,generation,version,agent,state,started,heartbeat,progress_at,workspace) VALUES(?,?,?,?,?,'running',?,?,?,?)",params![aid,tid,generation,t["version"].as_i64(),agent,now(),now(),now(),workspace])?;
    db.execute(
        "UPDATE attempts SET reserved_children=? WHERE id=?",
        params![reserve_children, aid],
    )?;
    db.execute(
        "UPDATE tasks SET state='running',generation=?,owner=?,reason='',updated=? WHERE id=?",
        params![generation, agent, now(), tid],
    )?;
    Ok(
        json!({"attempt":aid,"task":tid,"generation":generation,"version":t["version"],"workspace":workspace,"state":"running","reserved_children":reserve_children}),
    )
}

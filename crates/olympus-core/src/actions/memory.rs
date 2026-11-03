use crate::*;

pub fn search(db: &Connection, actor: &Principal, r: &Value) -> Result<Value> {
    let q = format!("%{}%", optional(r, "query"));
    let project = optional(r, "project");
    if !project.is_empty() {
        actor.scope(&project)?;
    }
    Ok(json!(rows(db,"SELECT * FROM knowledge WHERE status!='superseded' AND (? OR project=? OR project IS NULL) AND (?='' OR project=? OR project IS NULL) AND (title LIKE ? OR body LIKE ?) ORDER BY created DESC LIMIT 50",params![actor.global(),actor.project,project,project,q,q])?))
}

fn manifest(db: &Connection, actor: &Principal, tid: &str) -> Result<Value> {
    let t = task(db, actor, tid)?;
    let project = t["project"].as_str().unwrap();
    let mut records = vec![
        ("task".to_string(), t.clone()),
        (
            "charter".to_string(),
            row(db, "SELECT * FROM projects WHERE id=?", [project])?,
        ),
    ];
    for (kind,query) in [
        ("requirement","SELECT * FROM requirements WHERE task=? ORDER BY version"),
        ("dependency","SELECT * FROM dependencies WHERE task=? ORDER BY depends_on"),
        ("artifact","SELECT * FROM artifacts WHERE task=? ORDER BY id"),
        ("message","SELECT * FROM messages WHERE task=? AND state NOT IN ('acted','superseded') ORDER BY id"),
        ("incident","SELECT * FROM incidents WHERE task=? AND state='open' ORDER BY id"),
        ("routing","SELECT * FROM task_routing WHERE task=?"),
        ("lesson","SELECT * FROM lessons WHERE task=? ORDER BY id"),
        ("notification","SELECT * FROM change_notices WHERE task=? ORDER BY id"),
        ("profile","SELECT p.* FROM attempt_profiles p JOIN attempts a ON a.id=p.attempt WHERE a.task=? ORDER BY p.attempt"),
        ("outcome","SELECT o.* FROM run_outcomes o JOIN attempts a ON a.id=o.attempt WHERE a.task=? ORDER BY o.attempt"),
        ("attempt","SELECT id,task,generation,version,agent,state,workspace,runtime_id,reserved_children FROM attempts WHERE task=? ORDER BY generation")
    ]{for (i,v) in rows(db,query,[tid])?.into_iter().enumerate(){records.push((format!("{kind}:{i}"),v));}}
    for (i,v) in rows(db,"SELECT * FROM knowledge WHERE (project=? OR project IS NULL) AND status='verified' ORDER BY id",[project])?.into_iter().enumerate(){records.push((format!("knowledge:{i}"),v));}
    Ok(json!(records
        .into_iter()
        .map(
            |(key, value)| json!({"key":key,"sha":hash(value.to_string().as_bytes()),"value":value})
        )
        .collect::<Vec<_>>()))
}

pub fn mutate(db: &Connection, actor: &Principal, r: &Value, home: &Path) -> Result<Value> {
    match required(r, "action")?.as_str() {
        "memory.put" => {
            let project = r["project"].as_str();
            if let Some(p) = project {
                actor.scope(p)?;
                row(db, "SELECT id FROM projects WHERE id=?", [p])?;
            } else {
                require_manager(actor)?;
                if !actor.global() {
                    bail!("shared knowledge requires chief or operator authority")
                }
            }
            let title = required(r, "title")?;
            let body = required(r, "body")?;
            let sources = r
                .get("sources")
                .filter(|s| s.is_array() && !s.as_array().unwrap().is_empty())
                .context("at least one provenance source is required")?;
            let status = optional(r, "status");
            let status = if status.is_empty() {
                "candidate"
            } else {
                &status
            };
            if !matches!(status, "candidate" | "verified") {
                bail!("invalid knowledge status")
            }
            if status == "verified" {
                require_manager(actor)?;
            }
            let kid = id("K");
            db.execute("INSERT INTO knowledge(id,project,title,body,sources,status,created) VALUES(?,?,?,?,?,?,?)",params![kid,project,title,body,sources.to_string(),status,now()])?;
            let text = format!(
                "# {title}\n\n{body}\n\nSources: {sources}\n\nStatus: {status}\nRecord: {kid}\n"
            );
            private_write(
                &home.join("vault").join(format!("{kid}.md")),
                text.as_bytes(),
            )?;
            Ok(json!({"knowledge":kid,"status":status,"vault_file":format!("vault/{kid}.md")}))
        }
        "memory.supersede" => {
            require_manager(actor)?;
            let old = required(r, "old")?;
            let new = required(r, "new")?;
            if old == new {
                bail!("cannot supersede a record with itself")
            }
            let a = row(db, "SELECT * FROM knowledge WHERE id=?", [&old])?;
            let b = row(db, "SELECT * FROM knowledge WHERE id=?", [&new])?;
            if a["project"] != b["project"] {
                bail!("knowledge scope mismatch")
            }
            if let Some(p) = a["project"].as_str() {
                actor.scope(p)?;
            } else if !actor.global() {
                bail!("shared knowledge requires chief authority")
            }
            if a["status"] == "superseded" || b["status"] == "superseded" {
                bail!("record already superseded")
            }
            db.execute(
                "UPDATE knowledge SET status='superseded' WHERE id=?",
                [&old],
            )?;
            db.execute(
                "UPDATE knowledge SET supersedes=? WHERE id=?",
                params![a["id"].as_str(), new],
            )?;
            for kid in [&old, &new] {
                let k = row(db, "SELECT * FROM knowledge WHERE id=?", [kid])?;
                let text = format!(
                    "# {}\n\n{}\n\nSources: {}\nStatus: {}\nSupersedes: {}\nRecord: {}\n",
                    k["title"].as_str().unwrap(),
                    k["body"].as_str().unwrap(),
                    k["sources"].as_str().unwrap(),
                    k["status"].as_str().unwrap(),
                    k["supersedes"],
                    kid
                );
                private_write(
                    &home.join("vault").join(format!("{kid}.md")),
                    text.as_bytes(),
                )?;
            }
            Ok(json!({"superseded":true,"original_retained":true}))
        }
        "context.create" => {
            let (t, a) = current_attempt(db, actor, r)?;
            let tid = t["id"].as_str().unwrap();
            if a["state"] != "paused" {
                bail!("pause at a safe checkpoint before taking a context manifest")
            }
            required(r, "checkpoint")?;
            let m = manifest(db, actor, tid)?;
            let cid = id("CTX");
            let digest = hash(m.to_string().as_bytes());
            db.execute("INSERT INTO capsules(id,task,generation,version,manifest,sha,created) VALUES(?,?,?,?,?,?,?)",params![cid,tid,t["generation"].as_i64(),t["version"].as_i64(),m.to_string(),digest,now()])?;
            let required = m
                .as_array()
                .unwrap()
                .iter()
                .map(|v| json!({"key":v["key"],"sha":v["sha"]}))
                .collect::<Vec<_>>();
            Ok(
                json!({"capsule":cid,"sha":digest,"required":required,"record_count":required.len()}),
            )
        }
        "context.verify" => {
            let cid = required(r, "capsule")?;
            let cap = row(db, "SELECT * FROM capsules WHERE id=?", [&cid])?;
            let tid = cap["task"].as_str().unwrap();
            let t = task(db, actor, tid)?;
            let successor = if actor.is_operator() {
                required(r, "successor")?
            } else {
                actor.id.clone()
            };
            let ag = row(
                db,
                "SELECT * FROM agents WHERE id=? AND state!='retired'",
                [&successor],
            )?;
            if ag["project"] != t["project"] || ag["role"] != t["role"] {
                bail!("successor scope or role mismatch")
            }
            if t["owner"] == successor {
                bail!("successor must be a different identity")
            }
            let m = manifest(db, actor, tid)?;
            if hash(m.to_string().as_bytes()) != cap["sha"].as_str().unwrap() {
                bail!("durable context changed; take a new manifest")
            }
            let expected = m
                .as_array()
                .unwrap()
                .iter()
                .map(|v| json!({"key":v["key"],"sha":v["sha"]}))
                .collect::<Vec<_>>();
            if r["reconstructed"] != json!(expected) {
                bail!("missing, reordered or changed required records; handover refused")
            }
            required(r, "next_action")?;
            db.execute(
                "UPDATE capsules SET verified_by=? WHERE id=?",
                params![successor, cid],
            )?;
            Ok(json!({"verified":true,"successor":successor,"ownership_transferred":false}))
        }
        "context.transfer" => {
            require_operator(actor)?;
            if r["confirmed_stopped"] != true {
                bail!("confirm the old runtime is stopped before transferring ownership")
            }
            let cid = required(r, "capsule")?;
            let cap = row(db, "SELECT * FROM capsules WHERE id=?", [cid])?;
            let tid = cap["task"].as_str().unwrap();
            let successor = cap["verified_by"]
                .as_str()
                .context("successor verification required")?;
            let t = task(db, actor, tid)?;
            let m = manifest(db, actor, tid)?;
            if t["state"] != "paused"
                || t["generation"] != cap["generation"]
                || hash(m.to_string().as_bytes()) != cap["sha"].as_str().unwrap()
            {
                bail!("handover became stale")
            }
            let busy:i64=db.query_row("SELECT COUNT(*) FROM attempts WHERE agent=? AND state IN ('running','unknown','paused','review','recovering')",[successor],|r|r.get(0))?;
            if busy > 0 {
                bail!("successor already owns an active attempt")
            }
            let old = row(
                db,
                "SELECT * FROM attempts WHERE task=? AND generation=?",
                params![tid, t["generation"].as_i64()],
            )?;
            db.execute(
                "UPDATE attempts SET state='fenced' WHERE id=?",
                [old["id"].as_str()],
            )?;
            let gen = t["generation"].as_i64().unwrap() + 1;
            let aid = id("RUN");
            db.execute("INSERT INTO attempts(id,task,generation,version,agent,state,started,heartbeat,progress_at,workspace) VALUES(?,?,?,?,?,'running',?,?,?,?)",params![aid,tid,gen,t["version"].as_i64(),successor,now(),now(),now(),old["workspace"].as_str()])?;
            db.execute(
                "UPDATE attempts SET reserved_children=? WHERE id=?",
                params![old["reserved_children"].as_i64(), aid],
            )?;
            db.execute(
                "UPDATE tasks SET generation=?,owner=?,state='running',updated=? WHERE id=?",
                params![gen, successor, now(), tid],
            )?;
            Ok(
                json!({"attempt":aid,"generation":gen,"successor":successor,"workspace_preserved":true}),
            )
        }
        _ => bail!("unknown memory action"),
    }
}

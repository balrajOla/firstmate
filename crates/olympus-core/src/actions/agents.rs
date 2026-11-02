use crate::*;

pub fn mutate(db: &Connection, actor: &Principal, r: &Value, home: &Path) -> Result<Value> {
    match required(r, "action")?.as_str() {
        "agent.create" => {
            require_manager(actor)?;
            let role = required(r, "role")?;
            if !matches!(
                role.as_str(),
                "chief"
                    | "captain"
                    | "manager"
                    | "engineer"
                    | "senior"
                    | "rust"
                    | "quality"
                    | "reviewer"
                    | "researcher"
            ) {
                bail!("unknown role package")
            }
            if role == "chief" && !actor.is_operator() {
                bail!("only operator may create a chief")
            }
            let project = if role == "chief" {
                None
            } else {
                let p = required(r, "project")?;
                actor.scope(&p)?;
                row(db, "SELECT id FROM projects WHERE id=?", [&p])?;
                Some(p)
            };
            let parent = r["parent"].as_str();
            let depth = if let Some(pid) = parent {
                let p = row(
                    db,
                    "SELECT * FROM agents WHERE id=? AND state!='retired'",
                    [pid],
                )?;
                if !matches!(p["role"].as_str(), Some("chief" | "captain" | "manager")) {
                    bail!("parent is not a manager")
                }
                if p["role"] != "chief" && p["project"].as_str() != project.as_deref() {
                    bail!("parent project mismatch")
                }
                if !actor.is_operator() && actor.role != "chief" && pid != actor.id {
                    bail!("create children only under your own identity")
                }
                let children: i64 = db.query_row(
                    "SELECT COUNT(*) FROM agents WHERE parent=? AND state!='retired'",
                    [pid],
                    |r| r.get(0),
                )?;
                if children >= if p["role"] == "chief" { 32 } else { 8 } {
                    bail!("manager span limit reached; create a justified sub-captain")
                }
                p["depth"].as_i64().unwrap() + 1
            } else {
                if !actor.global() {
                    bail!("project managers must specify their own parent identity")
                };
                0
            };
            if depth > 4 {
                bail!("organisation depth limit is four")
            }
            let aid = id("AG");
            let token = uuid::Uuid::new_v4().simple().to_string();
            db.execute("INSERT INTO agents(id,project,parent,role,harness,model,token_hash,depth,created) VALUES(?,?,?,?,?,?,?,?,?)",params![aid,project,parent,role,optional(r,"harness"),optional(r,"model"),hash(token.as_bytes()),depth,now()])?;
            private_dir(&home.join("credentials"))?;
            let file = home.join("credentials").join(format!("{aid}.token"));
            private_write(&file, token.as_bytes())?;
            Ok(
                json!({"agent":aid,"role":role,"credential_file":file,"depth":depth,"instruction":role_instruction(&role)}),
            )
        }
        "agent.activity" => {
            let aid = if actor.is_operator() {
                required(r, "agent")?
            } else {
                actor.id.clone()
            };
            let state = required(r, "state")?;
            if !matches!(state.as_str(), "working" | "idle") {
                bail!("invalid agent activity")
            }
            if db.execute(
                "UPDATE agents SET state=?,last_seen=? WHERE id=? AND state!='retired'",
                params![state, now(), aid],
            )? != 1
            {
                bail!("agent not found")
            }
            Ok(json!({"state":state,"observed":now()}))
        }
        "child.reserve" => {
            let (parent_task, parent_attempt) = current_attempt(db, actor, r)?;
            if parent_task["state"] != "running" {
                bail!("only a running owner can reserve a child")
            }
            let parent = row(
                db,
                "SELECT * FROM agents WHERE id=?",
                [parent_attempt["agent"].as_str()],
            )?;
            let depth = parent["depth"].as_i64().unwrap() + 1;
            if depth > 6 {
                bail!("native child depth limit reached")
            }
            let role = required(r, "role")?;
            if !matches!(
                role.as_str(),
                "engineer" | "rust" | "researcher" | "quality" | "reviewer"
            ) {
                bail!("child must be a bounded specialist")
            }
            let tid = id("T");
            let aid = id("AG");
            let body = required(r, "requirements")?;
            let title = required(r, "title")?;
            let token = uuid::Uuid::new_v4().simple().to_string();
            db.execute("INSERT INTO agents(id,project,parent,role,harness,model,token_hash,depth,created) VALUES(?,?,?,?,?,?,?,?,?)",params![aid,parent["project"].as_str(),parent["id"].as_str(),role,parent["harness"].as_str(),parent["model"].as_str(),hash(token.as_bytes()),depth,now()])?;
            db.execute("INSERT INTO tasks(id,project,title,requirements,role,created,updated) VALUES(?,?,?,?,?,?,?)",params![tid,parent_task["project"].as_str(),title,body,role,now(),now()])?;
            db.execute(
                "INSERT INTO requirements VALUES(?,1,?,?)",
                params![tid, body, now()],
            )?;
            if parent_attempt["reserved_children"].as_i64().unwrap_or(0) > 0 {
                db.execute(
                    "UPDATE attempts SET reserved_children=reserved_children-1 WHERE id=?",
                    [parent_attempt["id"].as_str()],
                )?;
            }
            let reservation =
                super::tasks::claim(db, &Principal::operator(), &json!({"task":tid,"agent":aid}))?;
            db.execute(
                "INSERT INTO dependencies VALUES(?,?)",
                params![parent_task["id"].as_str(), tid],
            )?;
            private_dir(&home.join("credentials"))?;
            let file = home.join("credentials").join(format!("{aid}.token"));
            private_write(&file, token.as_bytes())?;
            Ok(
                json!({"agent":aid,"task":tid,"reservation":reservation,"credential_file":file,"execution":"reserved; use an independently verified native adapter, otherwise a brokered worker","instruction":role_instruction(&role)}),
            )
        }
        "agent.retire" => {
            require_manager(actor)?;
            let aid = required(r, "agent")?;
            let a = row(db, "SELECT * FROM agents WHERE id=?", [&aid])?;
            actor.scope(a["project"].as_str().unwrap_or(""))?;
            let active:i64=db.query_row("SELECT COUNT(*) FROM attempts WHERE agent=? AND state IN ('running','unknown','review','paused','recovering')",[&aid],|r|r.get(0))?;
            let children: i64 = db.query_row(
                "SELECT COUNT(*) FROM agents WHERE parent=? AND state!='retired'",
                [&aid],
                |r| r.get(0),
            )?;
            if active + children > 0 {
                bail!("reconcile active work and child identities before retirement")
            }
            db.execute("UPDATE agents SET state='retired' WHERE id=?", [aid])?;
            Ok(json!({"retired":true}))
        }
        "message.send" => {
            let recipient = required(r, "recipient")?;
            let dest = row(
                db,
                "SELECT id,project FROM agents WHERE id=? AND state!='retired'",
                [&recipient],
            )?;
            let tid = r["task"].as_str();
            let (version, generation) = if let Some(tid) = tid {
                let t = task(db, actor, tid)?;
                (t["version"].as_i64(), t["generation"].as_i64())
            } else {
                (None, None)
            };
            if !actor.global() && dest["project"].as_str() != actor.project.as_deref() {
                let linked:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM dependencies d JOIN tasks a ON a.id=d.task JOIN tasks b ON b.id=d.depends_on WHERE (a.project=? AND b.project=?) OR (b.project=? AND a.project=?))",params![actor.project,dest["project"].as_str(),actor.project,dest["project"].as_str()],|r|r.get(0))?;
                if !linked || tid.is_none() {
                    bail!("cross-project communication needs an explicit dependency contract and task")
                }
            }
            let priority = optional(r, "priority");
            let priority = if priority.is_empty() {
                "normal"
            } else {
                &priority
            };
            if !matches!(priority, "critical" | "normal" | "progress") {
                bail!("invalid message priority")
            }
            let body = required(r, "body")?;
            let thread = required(r, "thread")?;
            let deadline = number(r, "deadline", now() + 300);
            if deadline <= now() {
                bail!("message deadline must be in the future")
            }
            if priority == "progress" {
                db.execute("UPDATE messages SET state='superseded' WHERE sender=? AND recipient=? AND thread=? AND priority='progress' AND state='persisted'",params![actor.id,recipient,thread])?;
            }
            let count:i64=db.query_row("SELECT COUNT(*) FROM messages WHERE recipient=? AND state IN ('persisted','delivered','read')",[&recipient],|r|r.get(0))?;
            if count >= if priority == "critical" { 160 } else { 128 } {
                bail!("mailbox capacity reached; retain the request and retry after backpressure clears")
            }
            let mid = id("M");
            db.execute("INSERT INTO messages(id,sender,recipient,task,version,generation,thread,body,priority,created,deadline) VALUES(?,?,?,?,?,?,?,?,?,?,?)",params![mid,actor.id,recipient,tid,version,generation,thread,body,priority,now(),deadline])?;
            Ok(
                json!({"message":mid,"state":"persisted","delivered":false,"read":false,"acted":false}),
            )
        }
        "message.ack" => {
            let mid = required(r, "message")?;
            let m = row(db, "SELECT * FROM messages WHERE id=?", [&mid])?;
            if !actor.is_operator() && m["recipient"] != actor.id {
                bail!("only the recipient may acknowledge")
            }
            if let Some(tid) = m["task"].as_str() {
                let t = row(db, "SELECT version,generation FROM tasks WHERE id=?", [tid])?;
                if t["version"] != m["version"] || t["generation"] != m["generation"] {
                    bail!("stale message; request a current-version message")
                }
            }
            let state = required(r, "state")?;
            let states = ["persisted", "delivered", "read", "acted"];
            let old = states
                .iter()
                .position(|s| Some(*s) == m["state"].as_str())
                .context("dead-letter or superseded message needs a new send")?;
            let new = states
                .iter()
                .position(|s| *s == state)
                .context("invalid acknowledgement state")?;
            if new < old || new > old + 1 {
                bail!("acknowledgements must progress persisted -> delivered -> read -> acted")
            }
            db.execute("UPDATE messages SET state=?,read_at=COALESCE(read_at,?),acted_at=COALESCE(acted_at,?) WHERE id=?",params![state,if new>=2{Some(now())}else{None},if new>=3{Some(now())}else{None},mid])?;
            Ok(json!({"message":mid,"state":state}))
        }
        "profile.register" => {
            require_operator(actor)?;
            let pid = required(r, "profile")?;
            let harness = required(r, "harness")?;
            if !matches!(
                harness.as_str(),
                "claude" | "codex" | "muse" | "pi" | "simulator"
            ) {
                bail!("unsupported harness")
            }
            db.execute("INSERT INTO profiles VALUES(?,?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET harness=excluded.harness,model=excluded.model,version=excluded.version,capabilities=excluded.capabilities,evidence=excluded.evidence,verified=0",params![pid,harness,required(r,"model")?,required(r,"version")?,r.get("capabilities").unwrap_or(&json!({})).to_string(),"{}"]) ?;
            Ok(json!({"profile":pid,"verified":false}))
        }
        "profile.verify" => {
            require_operator(actor)?;
            let pid = required(r, "profile")?;
            let p = row(db, "SELECT * FROM profiles WHERE id=?", [&pid])?;
            let e = r.get("evidence").context("evidence required")?;
            if e["version"] != p["version"] || e["harness"] != p["harness"] {
                bail!("evidence does not match the registered version")
            }
            required(e, "command")?;
            required(e, "observed_result")?;
            for check in ["launch", "message", "interrupt", "resume", "stop"] {
                if e["checks"][check] != true {
                    bail!("live capability check missing or failed: {check}")
                }
            }
            if p["harness"] != "simulator" && e["real_harness"] != true {
                bail!("simulated evidence cannot enable a real harness")
            }
            let capabilities: Value = serde_json::from_str(p["capabilities"].as_str().unwrap())?;
            if capabilities["native_children"] == true {
                for check in [
                    "child_launch",
                    "child_message",
                    "child_stop",
                    "child_budget",
                ] {
                    if e["checks"][check] != true {
                        bail!("native child conformance missing: {check}")
                    }
                }
            }
            db.execute(
                "UPDATE profiles SET verified=1,evidence=? WHERE id=?",
                params![e.to_string(), pid],
            )?;
            Ok(
                json!({"profile":pid,"verified":true,"evidence_source":"operator-attested live conformance run"}),
            )
        }
        "system.configure" => {
            require_operator(actor)?;
            for key in ["global_cap", "project_cap"] {
                if let Some(n) = r.get(key) {
                    let n = n
                        .as_i64()
                        .filter(|n| *n >= 1 && *n <= 100)
                        .context("capacity must be 1..100")?;
                    db.execute(
                        "UPDATE settings SET value=? WHERE key=?",
                        params![n.to_string(), key],
                    )?;
                }
            }
            if let Some(enabled) = r.get("dispatch_enabled") {
                let b = enabled
                    .as_bool()
                    .context("dispatch_enabled must be boolean")?;
                db.execute(
                    "UPDATE settings SET value=? WHERE key='dispatch_enabled'",
                    [b.to_string()],
                )?;
            }
            Ok(json!({"configured":true}))
        }
        "operation.prepare" => {
            require_manager(actor)?;
            let tid = required(r, "task")?;
            let t = task(db, actor, &tid)?;
            let dispatch: String = db.query_row(
                "SELECT value FROM settings WHERE key='dispatch_enabled'",
                [],
                |r| r.get(0),
            )?;
            if dispatch != "true" {
                bail!("live dispatch is disabled")
            }
            let profile = required(r, "profile")?;
            let p = row(
                db,
                "SELECT * FROM profiles WHERE id=? AND verified=1",
                [&profile],
            )?;
            if p["harness"] == "simulator" {
                bail!("simulator profile cannot prepare live runtime operations")
            }
            let attempt=row(db,"SELECT * FROM attempts WHERE task=? AND generation=? AND state IN ('running','unknown','paused','review')",params![tid,t["generation"].as_i64()])?;
            let verb = required(r, "verb")?;
            if !matches!(
                verb.as_str(),
                "launch" | "message" | "interrupt" | "exit" | "relaunch"
            ) {
                bail!("unsupported lifecycle verb")
            }
            let pending: i64 = db.query_row(
                "SELECT COUNT(*) FROM operations WHERE task=? AND state IN ('pending','uncertain')",
                [&tid],
                |r| r.get(0),
            )?;
            if pending > 0 {
                bail!("reconcile the pending operation before another runtime effect")
            }
            let op = id("OP");
            db.execute(
                "INSERT INTO operations(id,task,attempt,verb,body,created) VALUES(?,?,?,?,?,?)",
                params![op, tid, attempt["id"].as_str(), verb, r.to_string(), now()],
            )?;
            Ok(
                json!({"operation":op,"state":"pending","attempt":attempt["id"],"harness":p["harness"],"model":p["model"]}),
            )
        }
        "operation.receipt" => {
            require_operator(actor)?;
            let op = required(r, "operation")?;
            let state = required(r, "state")?;
            if !matches!(state.as_str(), "confirmed" | "uncertain" | "refused") {
                bail!("invalid effect receipt")
            }
            let existing = row(db, "SELECT * FROM operations WHERE id=?", [&op])?;
            if existing["state"] == "confirmed" {
                bail!("operation is already confirmed")
            }
            let receipt = required(r, "receipt")?;
            db.execute(
                "UPDATE operations SET state=?,receipt=? WHERE id=?",
                params![state, receipt, op],
            )?;
            if state == "uncertain" {
                incident(
                    db,
                    existing["task"].as_str(),
                    "uncertain_effect",
                    "Do not replay until runtime effects are reconciled",
                    "operator",
                )?;
            }
            Ok(json!({"operation":op,"state":state}))
        }
        _ => bail!("unknown agent action"),
    }
}

pub fn role_instruction(role: &str) -> &'static str {
    match role {
        "chief"=>"Own portfolio priorities and exceptions. Use chief.briefing, delegate to scoped captains, preserve durable obligations, and finish each turn with a justified disposition. Never stop the service.",
        "captain"|"manager"=>"Own one charter and acceptance graph. Choose inline work for tightly coupled actions, native children for bounded questions, independent workers for durable or isolated work, and sub-captains only for a coherent deliverable. Route peer messages directly; escalate unresolved decisions.",
        "rust"=>"Implement Rust work using Cargo, ownership and concurrency invariants. Preserve uncommitted work, record checkpoints and code revisions, and submit candidate artifacts for independent verification.",
        "quality"|"reviewer"=>"Verify the current requirements and exact code revision independently. Exercise unit, integration and user journeys. Reject stale evidence and self-review; record reproducible failures and acceptance evidence.",
        "researcher"=>"Use attributable sources, separate evidence from inference, preserve provenance and uncertainty, and return a bounded artifact with acceptance evidence.",
        _=>"Own the assigned deliverable. Respect current requirements and scope, send peer questions through durable messages, checkpoint useful progress, and submit results for independent review. Do not claim completion from process exit."
    }
}

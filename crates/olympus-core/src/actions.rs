use crate::*;
mod agents;
mod memory;
mod planning;
mod tasks;

pub fn is_read(action: &str) -> bool {
    if matches!(
        action,
        "agenda"
            | "schedules.list"
            | "project.outcomes"
            | "project.context"
            | "memory.get"
            | "automation.status"
            | "backups.list"
    ) {
        return true;
    }
    matches!(
        action,
        "snapshot"
            | "projects.list"
            | "project.get"
            | "task.get"
            | "task.next"
            | "history"
            | "response.get"
            | "message.inbox"
            | "message.get"
            | "memory.search"
            | "context.get"
            | "events"
            | "profiles.list"
            | "intake.preview"
            | "chief.briefing"
            | "operations.list"
            | "dispatch.plan"
    )
}

pub fn read(db: &Connection, actor: &Principal, req: &Value) -> Result<Value> {
    let action = required(req, "action")?;
    let scope = actor.project.as_deref().unwrap_or("");
    match action.as_str(){
        "agenda"|"schedules.list"=>crate::scheduling::read(db,actor,req),
        "project.outcomes"=>crate::portfolio::outcomes(db,actor,req),
        "project.context"=>crate::portfolio::context(db,actor,&required(req,"project")?),
        "memory.get"=>{let item=row(db,"SELECT * FROM knowledge WHERE id=?",[required(req,"knowledge")?])?;if let Some(p)=item["project"].as_str(){actor.scope(p)?;}Ok(item)},
        "automation.status"=>Ok(json!(rows(db,"SELECT a.*,p.name AS project_name FROM autonomy a JOIN projects p ON p.id=a.project WHERE (? OR a.project=?) ORDER BY p.name",params![actor.global(),scope])?)),
        "backups.list"=>{require_operator(actor)?;Ok(json!(rows(db,"SELECT * FROM backup_runs ORDER BY created DESC LIMIT 20",[])?))},
        "project.get"=>{let pid=required(req,"project")?;actor.scope(&pid)?;row(db,"SELECT * FROM projects WHERE id=?",[pid])},
        "projects.list"=>Ok(json!(rows(db,"SELECT * FROM projects WHERE (? OR id=?) ORDER BY created,id",params![actor.global(),scope])?)),
        "snapshot"=>{
            let projects=rows(db,"SELECT * FROM projects WHERE (? OR id=?) ORDER BY created,id LIMIT 100",params![actor.global(),scope])?;
            let tasks=rows(db,"SELECT t.*,p.name AS project_name,a.heartbeat,a.progress_at,a.fingerprint,a.runtime_id FROM tasks t JOIN projects p ON p.id=t.project LEFT JOIN attempts a ON a.task=t.id AND a.generation=t.generation WHERE (? OR t.project=?) AND t.state NOT IN ('accepted','cancelled') ORDER BY CASE t.state WHEN 'running' THEN 0 WHEN 'unknown' THEN 1 WHEN 'recovering' THEN 2 ELSE 3 END,t.priority DESC,t.created,t.id LIMIT 500",params![actor.global(),scope])?;
            let agents=rows(db,"SELECT id,project,parent,role,harness,model,depth,state,last_seen FROM agents WHERE state!='retired' AND (? OR project=?) ORDER BY depth,created,id LIMIT 500",params![actor.global(),scope])?;
            let incidents=rows(db,"SELECT i.*,t.project,t.title,p.name AS project_name FROM incidents i LEFT JOIN tasks t ON t.id=i.task LEFT JOIN projects p ON p.id=t.project WHERE i.state='open' AND (? OR t.project=?) ORDER BY i.created,i.id LIMIT 500",params![actor.global(),scope])?;
            let responses=rows(db,"SELECT r.*,p.name AS project_name FROM responses r LEFT JOIN projects p ON p.id=r.project WHERE (? OR r.project=?) ORDER BY seq DESC LIMIT 5",params![actor.global(),scope])?;
            let completed=rows(db,"SELECT t.*,p.name AS project_name FROM tasks t JOIN projects p ON p.id=t.project WHERE t.state='accepted' AND (? OR t.project=?) ORDER BY t.completed_seq DESC LIMIT 10",params![actor.global(),scope])?;
            let counts=row(db,"SELECT COALESCE(SUM(t.state='running'),0) AS working,COALESCE(SUM(t.state='recovering'),0) AS recovering,COALESCE(SUM(t.state='blocked'),0) AS blocked,COALESCE(SUM(t.state='unknown'),0) AS unknown,COALESCE(SUM(t.state='running' AND a.heartbeat>=?),0) AS fresh_active FROM tasks t LEFT JOIN attempts a ON a.task=t.id AND a.generation=t.generation WHERE (? OR t.project=?)",params![now()-30,actor.global(),scope])?;
            let mut counts=counts;counts["needs_you"]=json!(db.query_row("SELECT COUNT(*) FROM incidents i LEFT JOIN tasks t ON t.id=i.task WHERE i.state='open' AND i.owner='operator' AND (? OR t.project=?)",params![actor.global(),scope],|r|r.get::<_,i64>(0))?);
            let settings=rows(db,"SELECT * FROM settings ORDER BY key",[])?;
            let observations=if actor.global(){rows(db,"SELECT observed,body FROM runtime_observations",[])?}else{vec![]};
            Ok(json!({"schema":SCHEMA,"generated_at":now(),"projects":compact(projects.clone()),"tasks":compact(tasks.clone()),"agents":compact(agents.clone()),"incidents":compact(incidents.clone()),"responses":compact(responses.clone()),"completed":compact(completed.clone()),"settings":settings,"runtime":compact(observations),"counts":counts,"chief":if agents.iter().any(|a|a["role"]=="chief"&&a["state"]=="working"&&a["last_seen"].as_i64().is_some_and(|t|now()-t<30)){"working"}else{"idle"},"history_retained":true}))
        },
        "task.get"=>{
            let id=required(req,"task")?;let t=task(db,actor,&id)?;
            Ok(json!({"task":t,"versions":rows(db,"SELECT * FROM requirements WHERE task=? ORDER BY version",[&id])?,"attempts":rows(db,"SELECT * FROM attempts WHERE task=? ORDER BY generation",[&id])?,"dependencies":rows(db,"SELECT * FROM dependencies WHERE task=?",[&id])?,"artifacts":rows(db,"SELECT * FROM artifacts WHERE task=? ORDER BY created",[&id])?,"incidents":rows(db,"SELECT * FROM incidents WHERE task=? ORDER BY created",[&id])?}))
        },
        "task.next"=>Ok(json!(rows(db,"SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project WHERE t.state='ready' AND p.state='active' AND (? OR t.project=?) AND NOT EXISTS(SELECT 1 FROM dependencies d JOIN tasks dep ON dep.id=d.depends_on WHERE d.task=t.id AND dep.state!='accepted') ORDER BY (SELECT COUNT(*) FROM attempts a JOIN tasks z ON z.id=a.task WHERE z.project=t.project AND a.state IN ('running','unknown','paused','review','recovering')), t.priority DESC,t.created,t.id LIMIT 20",params![actor.global(),scope])?)),
        "history"=>{
            let limit=number(req,"limit",10).clamp(1,100);let before=number(req,"before",i64::MAX);
            let project=optional(req,"project");if !project.is_empty(){actor.scope(&project)?;}
            let global=actor.global()&&project.is_empty();let scope=if project.is_empty(){scope}else{&project};
            let kind=optional(req,"kind");let search=format!("%{}%",optional(req,"search"));
            if kind=="projects" {Ok(json!(rows(db,"SELECT completed_seq AS cursor,* FROM projects WHERE state='completed' AND completed_seq<? AND (? OR id=?) AND name LIKE ? ORDER BY completed_seq DESC LIMIT ?",params![before,global,scope,search,limit])?))}
            else if kind=="tasks" {Ok(json!(rows(db,"SELECT t.completed_seq AS cursor,t.*,p.name AS project_name FROM tasks t JOIN projects p ON p.id=t.project WHERE t.state='accepted' AND t.completed_seq<? AND (? OR t.project=?) AND (t.title LIKE ? OR t.requirements LIKE ?) ORDER BY t.completed_seq DESC LIMIT ?",params![before,global,scope,search,search,limit])?))}
            else {Ok(json!(rows(db,"SELECT r.seq AS cursor,r.*,p.name AS project_name FROM responses r LEFT JOIN projects p ON p.id=r.project WHERE r.seq<? AND (? OR r.project=?) AND r.body LIKE ? ORDER BY r.seq DESC LIMIT ?",params![before,global,scope,search,limit])?))}
        },
        "response.get"=>{
            let r=row(db,"SELECT * FROM responses WHERE id=?",[required(req,"response")?])?;
            actor.scope(r["project"].as_str().unwrap_or(""))?;Ok(r)
        },
        "message.inbox"=>{
            let recipient=if actor.is_operator(){required(req,"recipient")?}else{actor.id.clone()};
            Ok(json!(compact(rows(db,"SELECT * FROM messages WHERE recipient=? AND state NOT IN ('acted','dead_letter','superseded') ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,created,id LIMIT 100",[recipient])?)))
        },
        "message.get"=>{
            let m=row(db,"SELECT * FROM messages WHERE id=?",[required(req,"message")?])?;
            if !actor.is_operator()&&m["recipient"]!=actor.id&&m["sender"]!=actor.id{bail!("message belongs to a different conversation")}
            Ok(m)
        },
        "events"=>{
            require_manager(actor)?;
            Ok(json!(rows(db,"SELECT e.* FROM events e LEFT JOIN tasks t ON e.entity=t.id WHERE e.seq>? AND (? OR t.project=? OR e.entity=?) ORDER BY e.seq LIMIT 100",params![number(req,"after",0),actor.global(),scope,scope])?))
        },
        "dispatch.plan"=>planning::dispatch(db,actor,req),
        "profiles.list"=>Ok(json!(rows(db,"SELECT * FROM profiles ORDER BY id",[])?)),
        "intake.preview"=>crate::portfolio::preview(db,actor,req),
        "memory.search"=>memory::search(db,actor,req),
        "context.get"=>{
            let mut r=row(db,"SELECT * FROM capsules WHERE id=?",[required(req,"capsule")?])?;task(db,actor,r["task"].as_str().unwrap_or(""))?;
            let manifest:Value=serde_json::from_str(r["manifest"].as_str().context("invalid manifest")?)?;
            if let Some(key)=req["key"].as_str(){return manifest.as_array().context("invalid manifest")?.iter().find(|item|item["key"]==key).cloned().context("record not found")}
            r["manifest"]=json!(manifest.as_array().context("invalid manifest")?.iter().map(|v|json!({"key":v["key"],"sha":v["sha"]})).collect::<Vec<_>>());
            r["usage"]=json!("Fetch one required record at a time with context.get and key; verify every hash before transfer.");Ok(r)
        },
        "chief.briefing"=>{
            require_manager(actor)?;
            let summaries=rows(db,"SELECT p.id,substr(p.name,1,100) AS name,p.state,COUNT(t.id) AS tasks,SUM(t.state='running') AS running,SUM(t.state='blocked') AS blocked,SUM(t.state='ready') AS ready FROM projects p LEFT JOIN tasks t ON t.project=p.id WHERE (? OR p.id=?) GROUP BY p.id ORDER BY p.created LIMIT 20",params![actor.global(),scope])?;
            let exceptions=rows(db,"SELECT i.id,i.task,i.kind,substr(i.reason,1,300) AS reason,i.owner FROM incidents i LEFT JOIN tasks t ON t.id=i.task WHERE i.state='open' AND (? OR t.project=?) ORDER BY i.next_check,i.created LIMIT 5",params![actor.global(),scope])?;
            let cursor:i64=db.query_row("SELECT COALESCE(MAX(e.seq),0) FROM events e LEFT JOIN tasks t ON e.entity=t.id WHERE e.kind NOT IN ('agent.activity','task.heartbeat','runtime.observe','chief.finish','system.tick') AND (? OR t.project=? OR e.entity=?)",params![actor.global(),scope,scope],|r|r.get(0))?;
            let finish=rows(db,"SELECT body FROM events WHERE actor=? AND kind='chief.finish' ORDER BY seq DESC LIMIT 1",[&actor.id])?;
            let due=finish.first().and_then(|v|v["body"].as_str()).and_then(|v|serde_json::from_str::<Value>(v).ok()).is_some_and(|v|v["disposition"]=="waiting"&&v["next_check"].as_i64().is_some_and(|t|t<=now()));
            Ok(json!({"projects":summaries,"exceptions":exceptions,"work_cursor":cursor,"due":due,"policy":"Delegate ready work, resolve an exception, record a timed wait, or report justified idle. Ending this turn does not stop the coordinator.","input_budget_chars":32000}))
        },
        "operations.list"=>{require_manager(actor)?;Ok(json!(rows(db,"SELECT o.* FROM operations o JOIN tasks t ON t.id=o.task WHERE (? OR t.project=?) ORDER BY o.created DESC LIMIT 100",params![actor.global(),scope])?))},
        _=>bail!("unknown read action: {action}")
    }
}

pub fn mutate(db: &Connection, actor: &Principal, req: &Value, home: &Path) -> Result<Value> {
    let action = required(req, "action")?;
    let result = match action.as_str() {
        "intake.submit" => crate::portfolio::submit(db, actor, req, home)?,
        "schedule.create" | "schedule.state" | "task.due" => {
            crate::scheduling::mutate(db, actor, req)?
        }
        "automation.configure" => crate::automation::configure(db, actor, req)?,
        "maintenance.configure" => {
            require_operator(actor)?;
            if req.get("timezone").is_some() {
                let zone = crate::scheduling::timezone(db, req)?;
                db.execute(
                    "UPDATE settings SET value=? WHERE key='timezone'",
                    [zone.to_string()],
                )?;
            }
            if let Some(interval) = req["backup_interval"].as_i64() {
                if interval != 0 && !(300..=604800).contains(&interval) {
                    bail!("backup_interval must be 0 or 300..604800 seconds")
                };
                db.execute(
                    "UPDATE settings SET value=? WHERE key='backup_interval'",
                    [interval.to_string()],
                )?;
            }
            json!({"configured":true})
        }
        "review.submit" => crate::automation::review(db, actor, req, home)?,
        "team.create" | "evaluation.record" => planning::mutate(db, actor, req, home)?,
        "project.create" => {
            require_manager(actor)?;
            if !actor.global() {
                bail!("only chief or operator may create a project")
            }
            let pid = id("P");
            let name = required(req, "name")?;
            let charter = required(req, "charter")?;
            let aliases = req.get("aliases").cloned().unwrap_or(json!([]));
            if !aliases.is_array()
                || aliases
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|a| !a.is_string() || a.as_str().unwrap().len() < 3)
            {
                bail!("aliases must be strings of at least three characters")
            }
            db.execute("INSERT INTO projects(id,name,aliases,charter,repository,created) VALUES(?,?,?,?,?,?)",params![pid,name,aliases.to_string(),charter,optional(req,"repository"),now()])?;
            json!({"project":pid})
        }
        "project.state" => {
            require_manager(actor)?;
            let pid = required(req, "project")?;
            actor.scope(&pid)?;
            let state = required(req, "state")?;
            if !matches!(state.as_str(), "active" | "paused" | "completed") {
                bail!("invalid project state")
            }
            let pending:i64=db.query_row("SELECT COUNT(*) FROM tasks WHERE project=? AND state NOT IN ('accepted','cancelled')",[&pid],|r|r.get(0))?;
            if state == "completed" && pending > 0 {
                bail!("project still has unfinished tasks")
            }
            if db.execute(
                "UPDATE projects SET state=?,completed=? WHERE id=?",
                params![
                    state,
                    if state == "completed" {
                        Some(now())
                    } else {
                        None
                    },
                    pid
                ],
            )? != 1
            {
                bail!("project not found")
            };
            if state == "completed" {
                event(
                    db,
                    actor,
                    "project.completed",
                    &pid,
                    &json!({"project":pid}),
                )?;
                db.execute(
                    "UPDATE projects SET completed_seq=? WHERE id=?",
                    params![db.last_insert_rowid(), pid],
                )?;
            }
            json!({"project":pid,"state":state})
        }
        "task.create" | "task.amend" | "task.ack_version" | "task.link" | "task.claim"
        | "task.heartbeat" | "task.progress" | "task.result" | "task.review" | "task.block"
        | "task.pause" | "task.resume" | "task.cancel" | "task.recover" | "incident.resolve" => {
            tasks::mutate(db, actor, req, home)?
        }
        "agent.create" | "agent.retire" | "agent.activity" | "child.reserve" | "message.send"
        | "message.ack" | "profile.register" | "profile.verify" | "system.configure"
        | "operation.prepare" | "operation.receipt" => agents::mutate(db, actor, req, home)?,
        "memory.put" | "memory.supersede" | "context.create" | "context.verify"
        | "context.transfer" => memory::mutate(db, actor, req, home)?,
        "runtime.observe" => {
            require_operator(actor)?;
            let body = req.get("snapshot").context("snapshot required")?;
            if body["schema"] != "fm-fleet-snapshot.v1" {
                bail!("unsupported Firstmate snapshot schema")
            }
            let refreshed = crate::bridge::apply_observation(db, body)?;
            json!({"observed_at":now(),"source":"Firstmate canonical snapshot","heartbeats_refreshed":refreshed})
        }
        "chief.finish" => {
            require_manager(actor)?;
            let disposition = required(req, "disposition")?;
            if !matches!(
                disposition.as_str(),
                "delegated" | "waiting" | "decision" | "result" | "idle"
            ) {
                bail!("invalid turn disposition")
            }
            let reason = required(req, "reason")?;
            let ready: i64 = db.query_row(
                "SELECT COUNT(*) FROM tasks WHERE state='ready' AND (? OR project=?)",
                params![actor.global(), actor.project],
                |r| r.get(0),
            )?;
            if disposition == "idle" && ready > 0 {
                bail!("ready work remains: delegate it or name the waiting condition")
            }
            if disposition == "waiting" && number(req, "next_check", 0) <= now() {
                bail!("waiting requires a future next_check")
            }
            json!({"turn_finished":true,"coordinator_running":true,"disposition":disposition,"reason":reason,"next_check":req.get("next_check")})
        }
        "system.tick" => {
            require_operator(actor)?;
            let stale = rows(
                db,
                "SELECT task FROM attempts WHERE state='running' AND heartbeat<?",
                [now() - 30],
            )?;
            for r in &stale {
                let tid = r["task"].as_str().unwrap();
                db.execute(
                    "UPDATE attempts SET state='unknown' WHERE task=? AND state='running'",
                    [tid],
                )?;
                db.execute("UPDATE tasks SET state='unknown',reason='No fresh observation; replacement prohibited until reconciled',updated=? WHERE id=?",params![now(),tid])?;
                incident(
                    db,
                    Some(tid),
                    "observation_stale",
                    "Last contact exceeded 30 seconds; inspect runtime before replacing",
                    "operator",
                )?;
            }
            let late=rows(db,"SELECT id,task,recipient FROM messages WHERE deadline<? AND state IN ('persisted','delivered','read')",[now()])?;
            for m in &late {
                db.execute(
                    "UPDATE messages SET state='dead_letter' WHERE id=?",
                    [m["id"].as_str()],
                )?;
                incident(
                    db,
                    m["task"].as_str(),
                    "message_deadline",
                    &format!(
                        "Message {} needs delivery or acknowledgement reconciliation",
                        m["id"]
                    ),
                    "operator",
                )?;
            }
            let automatic = crate::automation::tick(db, home)?;
            json!({"stale_attempts":stale.len(),"dead_letters":late.len(),"checked_at":now(),"automatic":automatic})
        }
        _ => bail!("unsupported action: {action}"),
    };
    if action != "system.tick" || result["stale_attempts"] != 0 || result["dead_letters"] != 0 {
        let entity = req["task"]
            .as_str()
            .or(req["project"].as_str())
            .unwrap_or(actor.id.as_str());
        // Credentials never enter the event stream.
        let mut safe = result.clone();
        if let Some(o) = safe.as_object_mut() {
            o.remove("token");
        }
        event(db, actor, &action, entity, &safe)?;
    }
    Ok(result)
}

// List projections are bounded; task.get, project.get and response.get retain exact source text.
fn compact(mut records: Vec<Value>) -> Vec<Value> {
    for record in &mut records {
        let mut truncated = false;
        if let Some(object) = record.as_object_mut() {
            for value in object.values_mut() {
                if let Some(text) = value.as_str() {
                    if text.len() > 1000 {
                        *value = json!(format!(
                            "{}… [open full record]",
                            text.chars().take(1000).collect::<String>()
                        ));
                        truncated = true;
                    }
                }
            }
            if truncated {
                object.insert("projection_truncated".into(), json!(true));
            }
        }
    }
    records
}

pub(crate) fn role_text(role: &str) -> &'static str {
    agents::role_instruction(role)
}

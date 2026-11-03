//! Evidence-backed adaptation, never self-certification of model capability.
use crate::*;

fn infer_complexity(text: &str) -> &'static str {
    let lower = text.to_lowercase();
    if [
        "security",
        "migration",
        "architecture",
        "distributed",
        "concurrency",
        "deadlock",
        "cryptograph",
    ]
    .iter()
    .any(|w| lower.contains(w))
    {
        "complex"
    } else if text.len() < 300
        && ["typo", "spelling", "rename", "formatting"]
            .iter()
            .any(|w| lower.contains(w))
    {
        "routine"
    } else {
        "standard"
    }
}

pub(crate) fn classify(db: &Connection, tid: &str, r: &Value, text: &str) -> Result<()> {
    let explicit = optional(r, "complexity");
    if explicit.is_empty() {
        let pinned: bool = db.query_row(
            "SELECT EXISTS(SELECT 1 FROM task_routing WHERE task=? AND source='explicit')",
            [tid],
            |r| r.get(0),
        )?;
        if pinned {
            return Ok(());
        }
    }
    let complexity = if explicit.is_empty() {
        infer_complexity(text)
    } else {
        explicit.as_str()
    };
    if !matches!(complexity, "routine" | "standard" | "complex") {
        bail!("complexity must be routine, standard or complex");
    }
    db.execute("INSERT INTO task_routing VALUES(?,?,?) ON CONFLICT(task) DO UPDATE SET complexity=excluded.complexity,source=excluded.source",params![tid,complexity,if explicit.is_empty(){"conservative text heuristic; editable"}else{"explicit"}])?;
    Ok(())
}

pub(crate) fn classification(db: &Connection, t: &Value) -> Result<Value> {
    let stored = rows(
        db,
        "SELECT complexity,source FROM task_routing WHERE task=?",
        [t["id"].as_str()],
    )?;
    let mut result = stored
        .first()
        .cloned()
        .unwrap_or_else(||json!({"complexity":infer_complexity(&format!("{}\n{}",t["title"].as_str().unwrap_or(""),t["requirements"].as_str().unwrap_or(""))),"source":"conservative text heuristic; editable"}));
    let failures:i64=db.query_row("SELECT COUNT(*) FROM lessons WHERE task=? AND version=? AND kind IN ('review_rejected','repeated_failure')",params![t["id"].as_str(),t["version"].as_i64()],|r|r.get(0))?;
    // Failure does not prove a model is weak. A more capable configured class
    // is a bounded next option, and only future accepted evidence scores it.
    if failures >= 2 {
        result["complexity"] = json!("complex");
        result["source"] = json!("two recorded failures; require a complex-capable profile");
    }
    result["failure_records"] = json!(failures);
    Ok(result)
}

pub(crate) fn signature(p: &Value) -> String {
    hash(
        json!([p["harness"], p["model"], p["version"], p["capabilities"]])
            .to_string()
            .as_bytes(),
    )
}

pub(crate) fn eligibility(db: &Connection, t: &Value, p: &Value) -> Result<Value> {
    let caps: Value =
        serde_json::from_str(p["capabilities"].as_str().context("invalid capabilities")?)?;
    let class = classification(db, t)?;
    let complexity = class["complexity"].as_str().unwrap();
    if !caps["roles"]
        .as_array()
        .is_some_and(|roles| roles.iter().any(|role| role == &t["role"]))
    {
        bail!("role capability absent");
    }
    let supported = if let Some(classes) = caps["complexities"].as_array() {
        classes.iter().any(|c| c == complexity)
    } else {
        complexity != "complex"
    };
    if !supported {
        bail!("profile does not declare the required {complexity} complexity");
    }
    let failures:i64=db.query_row("SELECT COUNT(*) FROM lessons l JOIN attempt_profiles p ON p.attempt=l.attempt WHERE l.task=? AND p.signature=? AND l.version=? AND l.kind IN ('review_rejected','repeated_failure')",params![t["id"].as_str(),signature(p),t["version"].as_i64()],|r|r.get(0))?;
    if failures >= 2 {
        bail!(
            "two failures on this task; select a different verified profile or request a decision"
        );
    }
    let effort = caps["effort_by_complexity"][complexity]
        .as_str()
        .unwrap_or("");
    Ok(json!({"complexity":complexity,"effort":effort,"signature":signature(p)}))
}

pub(crate) fn metrics(db: &Connection, t: &Value, p: &Value, complexity: &str) -> Result<Value> {
    row(db,"SELECT COUNT(*) AS samples,COALESCE(SUM(outcome='accepted'),0) AS accepted,COALESCE(SUM(outcome='accepted_after_rework'),0) AS accepted_after_rework,COALESCE(AVG(CASE outcome WHEN 'accepted' THEN 1.0 WHEN 'accepted_after_rework' THEN 0.5 ELSE 0.0 END),0.0) AS quality_score,AVG(elapsed_ms) AS mean_ms FROM (SELECT o.* FROM run_outcomes o WHERE o.signature=? AND o.role=? AND o.complexity=? ORDER BY o.updated DESC,o.attempt LIMIT 100)",params![signature(p),t["role"].as_str(),complexity])
}

pub(crate) fn bind(db: &Connection, t: &Value, a: &Value, p: &Value) -> Result<Value> {
    let selection = eligibility(db, t, p)?;
    let previous = rows(
        db,
        "SELECT * FROM attempt_profiles WHERE attempt=?",
        [a["id"].as_str()],
    )?;
    if let Some(old) = previous.first() {
        if old["profile"] != p["id"] || old["signature"] != selection["signature"] {
            bail!(
                "attempt profile is pinned; stop and recover before changing model configuration"
            );
        }
        return Ok(old.clone());
    }
    db.execute(
        "INSERT INTO attempt_profiles VALUES(?,?,?,?,?,?)",
        params![
            a["id"].as_str(),
            p["id"].as_str(),
            selection["signature"].as_str(),
            selection["complexity"].as_str(),
            selection["effort"].as_str(),
            p.to_string()
        ],
    )?;
    row(
        db,
        "SELECT * FROM attempt_profiles WHERE attempt=?",
        [a["id"].as_str()],
    )
}

pub(crate) fn current_profile(db: &Connection, tid: &str) -> Result<Option<Value>> {
    let selected=rows(db,"SELECT p.* FROM attempt_profiles p JOIN attempts a ON a.id=p.attempt JOIN tasks t ON t.id=a.task AND t.generation=a.generation WHERE t.id=?",[tid])?;
    selected.first().map(|p| -> Result<Value> {
        let spec:Value=serde_json::from_str(p["specification"].as_str().unwrap())?;
        Ok(json!({"profile":p["profile"],"harness":spec["harness"],"model":spec["model"],"effort":p["effort"],"signature":p["signature"]}))
    }).transpose()
}

pub(crate) fn feedback(
    db: &Connection,
    t: &Value,
    attempt: &str,
    kind: &str,
    source: &str,
    body: &str,
) -> Result<()> {
    let inserted = db.execute(
        "INSERT OR IGNORE INTO lessons VALUES(?,?,?,?,?,?,?,?,?)",
        params![
            id("L"),
            t["project"].as_str(),
            t["id"].as_str(),
            attempt,
            kind,
            source,
            body,
            now(),
            t["version"].as_i64()
        ],
    )?;
    if inserted == 0 {
        return Ok(());
    }
    let binding=rows(db,"SELECT p.*,a.started FROM attempt_profiles p JOIN attempts a ON a.id=p.attempt WHERE p.attempt=?",[attempt])?;
    if let Some(p) = binding.first() {
        let failures:i64=db.query_row("SELECT COUNT(*) FROM lessons WHERE attempt=? AND kind IN ('review_rejected','repeated_failure')",[attempt],|r|r.get(0))?;
        let outcome = if kind == "accepted" {
            if failures == 0 {
                "accepted"
            } else {
                "accepted_after_rework"
            }
        } else {
            "failed"
        };
        db.execute("INSERT INTO run_outcomes VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(attempt) DO UPDATE SET outcome=excluded.outcome,failures=excluded.failures,elapsed_ms=excluded.elapsed_ms,updated=excluded.updated",params![attempt,p["profile"].as_str(),p["signature"].as_str(),t["role"].as_str(),p["complexity"].as_str(),outcome,failures,(now()-p["started"].as_i64().unwrap()).max(0)*1000,now()])?;
    }
    Ok(())
}

pub(crate) fn read(db: &Connection, actor: &Principal, r: &Value) -> Result<Value> {
    let pid = required(r, "project")?;
    actor.scope(&pid)?;
    let limit = number(r, "limit", 20).clamp(1, 50);
    let lessons=rows(db,"SELECT id,task,attempt,kind,source,version,substr(body,1,1200) AS evidence,created FROM lessons WHERE project=? ORDER BY created DESC,rowid DESC LIMIT ?",params![pid,limit])?;
    let outcomes=rows(db,"SELECT o.*,a.task FROM run_outcomes o JOIN attempts a ON a.id=o.attempt JOIN tasks t ON t.id=a.task WHERE t.project=? ORDER BY o.updated DESC,o.attempt LIMIT ?",params![pid,limit])?;
    Ok(
        json!({"project":pid,"lessons":lessons,"outcomes":outcomes,"policy":"Recorded failures are observations, not proven fixes. Accepted evidence is independently reviewed. Routing uses at least five outcomes for the same role, complexity and exact profile configuration. This is harness policy and memory, not model fine-tuning."}),
    )
}

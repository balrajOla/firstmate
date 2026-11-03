use crate::*;

pub fn dispatch(db: &Connection, actor: &Principal, r: &Value) -> Result<Value> {
    let tid = required(r, "task")?;
    let t = task(db, actor, &tid)?;
    let classification = crate::learning::classification(db, &t)?;
    let mut candidates = vec![];
    let mut excluded = vec![];
    for p in rows(db, "SELECT * FROM profiles ORDER BY id", [])? {
        let capabilities: Value = serde_json::from_str(p["capabilities"].as_str().unwrap())?;
        if p["verified"] != 1 || p["harness"] == "simulator" {
            excluded.push(json!({"profile":p["id"],"reason":if p["verified"]!=1{"not lifecycle verified"}else{"simulation only"}}));
            continue;
        }
        let selection = match crate::learning::eligibility(db, &t, &p) {
            Ok(v) => v,
            Err(e) => {
                excluded.push(json!({"profile":p["id"],"reason":e.to_string()}));
                continue;
            }
        };
        let metrics =
            crate::learning::metrics(db, &t, &p, selection["complexity"].as_str().unwrap())?;
        let n = metrics["samples"].as_i64().unwrap_or(0);
        let score = if n >= 5 {
            metrics["quality_score"].as_f64().unwrap_or(0.0)
        } else {
            0.5
        };
        candidates.push(json!({"profile":p["id"],"harness":p["harness"],"model":p["model"],"complexity":selection["complexity"],"effort":selection["effort"],"score":score,"evidence":metrics,"confidence":if n<5{"insufficient local evidence"}else{"local independently reviewed outcomes for this exact role, complexity and profile"},"native_children":capabilities["native_children"]==true}));
    }
    candidates.sort_by(|a, b| {
        b["score"]
            .as_f64()
            .unwrap()
            .total_cmp(&a["score"].as_f64().unwrap())
            .then_with(|| a["profile"].as_str().cmp(&b["profile"].as_str()))
    });
    let selected = candidates.first().cloned().unwrap_or(Value::Null);
    let shape = if r["tightly_coupled"] == true {
        "inline"
    } else if r["coherent_deliverables"].as_i64().unwrap_or(0) > 3 {
        "sub_captain"
    } else if r["bounded_question"] == true && selected["native_children"] == true {
        "reserved_native_child"
    } else {
        "independent_worker"
    };
    Ok(
        json!({"task":tid,"classification":classification,"shape":shape,"recommended":selected,"candidates":candidates,"excluded":excluded,"policy":"Rank only verified role and complexity capable profiles. Effort uses the operator-verified per-model mapping. Five matching automatic outcomes are needed for quality ranking; equal or unevidenced profiles use stable ordering. Two failures on this task exclude the same profile configuration. Native children require admission and live conformance. No universal best-model claim.","mutated":false}),
    )
}

pub fn mutate(db: &Connection, actor: &Principal, r: &Value, home: &Path) -> Result<Value> {
    require_manager(actor)?;
    match required(r, "action")?.as_str() {
        "team.create" => {
            let project = required(r, "project")?;
            actor.scope(&project)?;
            let roles = r["roles"].as_array().context("roles array required")?;
            if roles.is_empty() || roles.len() > 8 {
                bail!("team needs one to eight specialists")
            }
            let mut captain_req = json!({"action":"agent.create","project":project,"role":"captain","harness":optional(r,"harness"),"model":optional(r,"model")});
            if let Some(parent) = r["parent"].as_str() {
                captain_req["parent"] = json!(parent)
            } else if !actor.global() {
                captain_req["parent"] = json!(actor.id)
            }
            let captain = super::agents::mutate(db, actor, &captain_req, home)?;
            let captain_actor = Principal {
                id: captain["agent"].as_str().unwrap().into(),
                project: Some(project.clone()),
                role: "captain".into(),
            };
            let mut specialists = vec![];
            for role in roles {
                let role = role.as_str().context("each role must be a string")?;
                if matches!(role, "chief" | "captain" | "manager") {
                    bail!("team roles must be specialists; form a separate justified sub-captain explicitly")
                }
                specialists.push(super::agents::mutate(db,&captain_actor,&json!({"action":"agent.create","project":project,"parent":captain["agent"],"role":role}),home)?);
            }
            Ok(
                json!({"captain":captain,"specialists":specialists,"runtimes_launched":0,"note":"Identities are durable; launch only the workers whose tasks are ready and admitted."}),
            )
        }
        "evaluation.record" => {
            require_operator(actor)?;
            let tid = required(r, "task")?;
            let t = task(db, actor, &tid)?;
            let profile = required(r, "profile")?;
            row(db, "SELECT id FROM profiles WHERE id=?", [&profile])?;
            let outcome = required(r, "outcome")?;
            if !matches!(outcome.as_str(), "accepted" | "failed" | "cancelled") {
                bail!("invalid evaluation outcome")
            }
            if outcome == "accepted" && t["state"] != "accepted" {
                bail!("acceptance evidence required before a successful evaluation")
            }
            let elapsed = number(r, "elapsed_ms", -1);
            if elapsed < 0 {
                bail!("nonnegative elapsed_ms required")
            }
            let cost = r["cost"].as_f64();
            if cost.is_some_and(|c| !c.is_finite() || c < 0.0) {
                bail!("invalid cost")
            }
            let exists: bool = db.query_row(
                "SELECT EXISTS(SELECT 1 FROM evaluations WHERE task=? AND profile=?)",
                params![tid, profile],
                |r| r.get(0),
            )?;
            if exists {
                bail!("evaluation already recorded for this task and profile")
            }
            let eid = id("EVAL");
            db.execute(
                "INSERT INTO evaluations VALUES(?,?,?,?,?,?,?)",
                params![eid, tid, profile, outcome, elapsed, cost, now()],
            )?;
            Ok(
                json!({"evaluation":eid,"source":"operator-attested outcome","measured_cost":cost.is_some()}),
            )
        }
        _ => bail!("unknown planning action"),
    }
}

//! Deterministic fixtures use real domain operations and a separate, labelled home.
use crate::*;

pub fn seed(store: &mut Store) -> Result<Value> {
    let count: i64 = store
        .db
        .query_row("SELECT COUNT(*) FROM projects", [], |r| r.get(0))?;
    if count > 0 {
        bail!("demo requires an empty home; existing projects are never replaced")
    }
    store
        .db
        .execute("UPDATE settings SET value='simulator' WHERE key='mode'", [])?;
    call(
        store,
        json!({"action":"system.configure","global_cap":32,"project_cap":4}),
    )?;
    let chief = call(
        store,
        json!({"action":"agent.create","role":"chief","harness":"pi"}),
    )?;
    let names = [
        "WebCraft",
        "Evaluation Lab",
        "Release Tools",
        "Research Notes",
        "API Platform",
        "Agent Skills",
        "Docs Studio",
        "Browser Workspaces",
        "LinkedIn Workflow",
        "Travel Planner",
        "Model Evaluation",
        "Quality Platform",
        "Learning Portal",
        "Portfolio Site",
        "Hosting Ops",
        "Test Reliability",
        "Second Brain",
        "Home Projects",
        "Data Pipeline",
        "Experiment Lab",
    ];
    let mut projects = vec![];
    for (i, name) in names.iter().enumerate() {
        let p = call(
            store,
            json!({"action":"project.create","name":name,"charter":format!("Deliver the agreed {name} milestone with independently verified evidence. Synthetic demo project."),"aliases":[name.to_lowercase()]}),
        )?;
        let pid = p["project"].as_str().unwrap();
        projects.push(pid.to_string());
        let captain = call(
            store,
            json!({"action":"agent.create","project":pid,"role":"captain","parent":chief["agent"],"harness":if i%2==0{"claude"}else{"codex"}}),
        )?;
        let worker = call(
            store,
            json!({"action":"agent.create","project":pid,"role":"rust","parent":captain["agent"],"harness":if i%3==0{"muse"}else{"codex"}}),
        )?;
        let reviewer = call(
            store,
            json!({"action":"agent.create","project":pid,"role":"quality","parent":captain["agent"],"harness":"claude"}),
        )?;
        for n in 0..2 {
            let t = call(
                store,
                json!({"action":"task.create","project":pid,"title":format!("{} accepted milestone {}",name,n+1),"requirements":"Demonstrate the acceptance flow with synthetic evidence.","role":"rust"}),
            )?;
            let tid = t["task"].as_str().unwrap();
            let a = call(
                store,
                json!({"action":"task.claim","task":tid,"agent":worker["agent"]}),
            )?;
            let result = call(
                store,
                json!({"action":"task.result","task":tid,"generation":a["generation"],"body":format!("{name}: sample milestone complete.\n\nThis fixture demonstrates retained full responses and artifact links.\nTests and results here are synthetic, not live product evidence."),"code_revision":"demo-fixture"}),
            )?;
            call(
                store,
                json!({"action":"task.review","artifact":result["artifact"],"reviewer":reviewer["agent"],"code_revision":"demo-fixture","passed":true,"evidence":"Simulator fixture acceptance; no real harness executed."}),
            )?;
        }
        if i < 16 {
            let t = call(
                store,
                json!({"action":"task.create","project":pid,"title":format!("{} current deliverable",name),"requirements":"Complete the current milestone and preserve its source evidence.","role":"rust"}),
            )?;
            let tid = t["task"].as_str().unwrap();
            let a = call(
                store,
                json!({"action":"task.claim","task":tid,"agent":worker["agent"]}),
            )?;
            if i % 5 == 1 {
                call(
                    store,
                    json!({"action":"task.block","task":tid,"generation":a["generation"],"reason":"Choose the acceptance threshold for the evaluation rubric","owner":"operator","next_check":now()+600}),
                )?;
            } else if i == 2 {
                store.db.execute("UPDATE tasks SET state='recovering',reason='Restoring a verified checkpoint (simulated)' WHERE id=?",[tid])?;
                store
                    .db
                    .execute("UPDATE attempts SET state='recovering' WHERE task=?", [tid])?;
            } else {
                call(
                    store,
                    json!({"action":"task.progress","task":tid,"generation":a["generation"],"fingerprint":format!("fixture-{i}"),"checkpoint":"Sample artifact checkpoint recorded"}),
                )?;
            }
        }
    }
    call(
        store,
        json!({"action":"system.configure","global_cap":16,"project_cap":4}),
    )?;
    Ok(
        json!({"mode":"simulator","projects":projects.len(),"chief":chief["agent"],"model_calls":0,"home":store.home}),
    )
}
fn call(store: &mut Store, mut request: Value) -> Result<Value> {
    request["request_id"] = json!(id("demo"));
    store.execute(&Principal::operator(), &request)
}
pub fn tick(store: &mut Store) -> Result<()> {
    // Fixture clocks refresh only in the explicitly labelled simulator database.
    store.db.execute(
        "UPDATE attempts SET heartbeat=? WHERE state='running'",
        [now()],
    )?;
    Ok(())
}

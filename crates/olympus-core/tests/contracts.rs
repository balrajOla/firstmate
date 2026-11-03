use olympus_core::{backup, demo, id, now, Principal, Store};
use serde_json::{json, Value};
use std::{
    fs,
    sync::{Arc, Barrier},
    thread,
};
use tempfile::TempDir;

fn calendar_stamp(s: &str) -> i64 {
    chrono::DateTime::parse_from_rfc3339(s).unwrap().timestamp()
}
fn enable_contract_automation(l: &mut Lab) {
    // Synthetic domain attestation only. This test never launches a vendor CLI.
    for (pid, role) in [("contract-rust", "rust"), ("contract-review", "quality")] {
        send(&mut l.store,json!({"action":"profile.register","profile":pid,"harness":"codex","model":"contract-fixture","version":"contract-fixture","capabilities":{"roles":[role]}})).unwrap();
        send(&mut l.store,json!({"action":"profile.verify","profile":pid,"evidence":{"harness":"codex","version":"contract-fixture","real_harness":true,"command":"Synthetic domain test; not a live verification","observed_result":"Synthetic domain test only","checks":{"launch":true,"message":true,"interrupt":true,"resume":true,"stop":true}}})).unwrap();
    }
    send(
        &mut l.store,
        json!({"action":"system.configure","dispatch_enabled":true,"global_cap":4,"project_cap":4}),
    )
    .unwrap();
    send(&mut l.store,json!({"action":"automation.configure","project":l.project,"enabled":true,"progress_timeout":60})).unwrap();
}

#[test]
fn calendar_observes_london_dst_and_never_duplicates_the_repeated_hour() {
    use chrono_tz::Europe::London;
    use olympus_core::scheduling::next;
    assert_eq!(
        next(
            "daily",
            London,
            "01:30",
            0,
            calendar_stamp("2026-03-28T12:00:00Z")
        )
        .unwrap(),
        calendar_stamp("2026-03-29T01:00:00Z")
    );
    let first = next(
        "daily",
        London,
        "01:30",
        0,
        calendar_stamp("2026-10-24T12:00:00Z"),
    )
    .unwrap();
    assert_eq!(first, calendar_stamp("2026-10-25T00:30:00Z"));
    assert_eq!(
        next("daily", London, "01:30", 0, first).unwrap(),
        calendar_stamp("2026-10-26T01:30:00Z")
    );
    assert_eq!(
        next(
            "weekdays",
            London,
            "09:00",
            0,
            calendar_stamp("2026-11-06T10:00:00Z")
        )
        .unwrap(),
        calendar_stamp("2026-11-09T09:00:00Z")
    );
}

#[test]
fn recurring_work_survives_restart_coalesces_missed_runs_and_waits_for_overlap() {
    let mut l = Lab::new();
    let s=send(&mut l.store,json!({"action":"schedule.create","project":l.project,"title":"Daily integrity check","requirements":"Verify imported counts","role":"rust","cadence":"daily","time":"09:00","timezone":"Europe/London"})).unwrap();
    l.store
        .db
        .execute(
            "UPDATE schedules SET next_run=? WHERE id=?",
            rusqlite::params![now() - 86400 * 3, s["schedule"].as_str()],
        )
        .unwrap();
    l.store.tick().unwrap();
    let jobs = send(&mut l.store, json!({"action":"agenda","view":"today"})).unwrap();
    assert_eq!(jobs["jobs"].as_array().unwrap().len(), 1);
    let tid = jobs["jobs"][0]["id"].as_str().unwrap().to_string();
    l.store
        .db
        .execute("UPDATE schedules SET next_run=?", [now() - 1])
        .unwrap();
    drop(l.store);
    l.store = Store::open(l.dir.path()).unwrap();
    l.store.tick().unwrap();
    assert_eq!(
        l.store
            .db
            .query_row("SELECT COUNT(*) FROM schedule_runs", [], |r| r
                .get::<_, i64>(0))
            .unwrap(),
        1
    );
    send(
        &mut l.store,
        json!({"action":"task.cancel","task":tid,"reason":"Occurrence reconciled"}),
    )
    .unwrap();
    l.store.tick().unwrap();
    l.store.tick().unwrap();
    assert_eq!(
        l.store
            .db
            .query_row("SELECT COUNT(*) FROM schedule_runs", [], |r| r
                .get::<_, i64>(0))
            .unwrap(),
        2
    );
}

#[test]
fn completed_project_followup_restores_context_without_rewriting_old_results() {
    let mut l = Lab::new();
    let old = l.task("Importer");
    l.claim(&old);
    let art = l.result(&old, 1);
    l.accept(&art);
    send(&mut l.store,json!({"action":"memory.put","project":l.project,"title":"Importer contract","body":"Retain invalid rows","sources":["design-decision"],"status":"verified"})).unwrap();
    send(
        &mut l.store,
        json!({"action":"project.state","project":l.project,"state":"completed"}),
    )
    .unwrap();
    let input = json!({"action":"intake.submit","text":"WebCraft importer now needs CSV support","role":"rust"});
    let new = send(&mut l.store, input.clone()).unwrap();
    assert_eq!(new["reopened"], true);
    assert!(new["context"]["knowledge"]
        .as_array()
        .unwrap()
        .iter()
        .any(|k| k["title"] == "Importer contract"));
    assert_eq!(send(&mut l.store, input).unwrap()["duplicate"], true);
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":old})).unwrap()["task"]["state"],
        "accepted"
    );
    assert_ne!(new["task"], old);
}

#[test]
fn ambiguous_topic_routing_does_not_modify_projects() {
    let mut l = Lab::new();
    send(
        &mut l.store,
        json!({"action":"project.create","name":"WebCraft Mobile","charter":"Mobile importer"}),
    )
    .unwrap();
    let before = send(&mut l.store, json!({"action":"snapshot"})).unwrap();
    assert!(send(
        &mut l.store,
        json!({"action":"intake.submit","text":"WebCraft Mobile importer update"})
    )
    .is_err());
    assert_eq!(
        send(&mut l.store, json!({"action":"snapshot"})).unwrap()["tasks"],
        before["tasks"]
    );
    assert!(send(&mut l.store,json!({"action":"intake.submit","text":"WebCraft Mobile importer update","project":l.project})).is_ok());
}

#[test]
fn project_outcomes_and_history_are_separate_and_scoped() {
    let mut l = Lab::new();
    let t = l.task("Importer");
    l.claim(&t);
    l.result(&t, 1);
    let other = send(
        &mut l.store,
        json!({"action":"project.create","name":"Other","charter":"Another project"}),
    )
    .unwrap();
    assert_eq!(
        send(
            &mut l.store,
            json!({"action":"project.outcomes","project":other["project"]})
        )
        .unwrap()["outputs"],
        json!([])
    );
    assert_eq!(
        send(
            &mut l.store,
            json!({"action":"history","project":other["project"]})
        )
        .unwrap(),
        json!([])
    );
    assert_eq!(
        send(
            &mut l.store,
            json!({"action":"project.outcomes","project":l.project})
        )
        .unwrap()["outputs"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert!(as_agent(
        &mut l.store,
        &l.worker,
        json!({"action":"project.context","project":other["project"]})
    )
    .is_err());
}

#[test]
fn cross_project_agents_exchange_messages_only_after_a_dependency_is_linked() {
    let mut l = Lab::new();
    let t = l.task("Producer");
    let p = send(
        &mut l.store,
        json!({"action":"project.create","name":"Consumer","charter":"Consume shared API"}),
    )
    .unwrap();
    let agent = send(
        &mut l.store,
        json!({"action":"agent.create","project":p["project"],"role":"engineer"}),
    )
    .unwrap();
    let task=send(&mut l.store,json!({"action":"task.create","project":p["project"],"title":"Consume API","requirements":"Wait for producer"})).unwrap();
    let request = json!({"action":"message.send","recipient":agent["agent"],"task":t,"thread":"shared-api","body":"Contract version 2"});
    assert!(as_agent(&mut l.store, &l.worker, request.clone()).is_err());
    send(
        &mut l.store,
        json!({"action":"task.link","task":task["task"],"depends_on":t}),
    )
    .unwrap();
    let message = as_agent(&mut l.store, &l.worker, request).unwrap();
    for state in ["delivered", "read", "acted"] {
        as_agent(
            &mut l.store,
            &agent,
            json!({"action":"message.ack","message":message["message"],"state":state}),
        )
        .unwrap();
    }
    let reply=as_agent(&mut l.store,&agent,json!({"action":"message.send","recipient":l.worker["agent"],"task":task["task"],"thread":"shared-api","body":"Consumer aligned"})).unwrap();
    assert_eq!(
        as_agent(
            &mut l.store,
            &l.worker,
            json!({"action":"message.get","message":reply["message"]})
        )
        .unwrap()["body"],
        "Consumer aligned"
    );
}

#[test]
fn automatic_dispatch_reserves_review_capacity_and_review_accepts_exact_result() {
    let mut l = Lab::new();
    enable_contract_automation(&mut l);
    let t = l.task("Ship importer");
    l.store.tick().unwrap();
    l.store.tick().unwrap();
    let state = send(&mut l.store, json!({"action":"task.get","task":t})).unwrap();
    assert_eq!(state["task"]["state"], "running");
    assert_eq!(state["attempts"][0]["reserved_children"], 1);
    assert_eq!(
        l.store
            .db
            .query_row("SELECT COUNT(*) FROM operations", [], |r| r
                .get::<_, i64>(0))
            .unwrap(),
        1
    );
    let a = l.result(&t, 1);
    l.store.tick().unwrap();
    let review: String = l
        .store
        .db
        .query_row(
            "SELECT task FROM review_jobs WHERE artifact=?",
            [a["artifact"].as_str()],
            |r| r.get(0),
        )
        .unwrap();
    let record = send(&mut l.store, json!({"action":"task.get","task":review})).unwrap();
    assert_eq!(record["task"]["state"], "running");
    let reviewer = json!({"credential_file":l.dir.path().join("credentials").join(format!("{}.token",record["task"]["owner"].as_str().unwrap()))});
    as_agent(&mut l.store,&reviewer,json!({"action":"review.submit","task":review,"generation":1,"passed":true,"code_revision":"abc123","evidence":"Independent exact-revision contract test"})).unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":t})).unwrap()["task"]["state"],
        "accepted"
    );
}

#[test]
fn automatic_recovery_waits_for_exact_fresh_stopped_evidence() {
    let mut l = Lab::new();
    enable_contract_automation(&mut l);
    let t = l.task("Recover import");
    l.store.tick().unwrap();
    l.store
        .db
        .execute("UPDATE operations SET state='confirmed'", [])
        .unwrap();
    l.store.db.execute("UPDATE attempts SET runtime_id='exact-runtime',runtime_generation='g1',progress_at=?,heartbeat=?",[now()-120,now()-120]).unwrap();
    l.store.tick().unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":t})).unwrap()["task"]["state"],
        "recovering"
    );
    l.store.tick().unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":t})).unwrap()["task"]["generation"],
        1
    );
    l.store
        .db
        .execute(
            "UPDATE operations SET state='confirmed' WHERE verb='exit'",
            [],
        )
        .unwrap();
    let stamp = chrono::DateTime::from_timestamp(now(), 0)
        .unwrap()
        .to_rfc3339();
    let snapshot = json!({"schema":"fm-fleet-snapshot.v1","tasks":[{"id":"exact-runtime","spawn_gen":"g1","remote":null,"current_state":{"state":"unknown","freshness":"fresh","observed_at":stamp},"endpoint":{"exists":false,"agent_alive":"not_checked","freshness":"fresh","observed_at":stamp}}]});
    send(
        &mut l.store,
        json!({"action":"runtime.observe","snapshot":snapshot}),
    )
    .unwrap();
    l.store.tick().unwrap();
    let state = send(&mut l.store, json!({"action":"task.get","task":t})).unwrap();
    assert_eq!(state["task"]["generation"], 2);
    assert_eq!(state["attempts"][0]["state"], "fenced");
    assert!(send(
        &mut l.store,
        json!({"action":"task.result","task":t,"generation":1,"body":"Late","code_revision":"old"})
    )
    .is_err());
}

#[test]
fn restores_pause_schedules_and_automation_until_reconciliation() {
    let mut l = Lab::new();
    enable_contract_automation(&mut l);
    send(&mut l.store,json!({"action":"schedule.create","project":l.project,"title":"Daily","requirements":"Check","cadence":"daily"})).unwrap();
    let snap = l.dir.path().join("copy");
    backup::create(&l.store, &snap).unwrap();
    let dest = l.dir.path().join("restore");
    backup::restore(&snap, &dest).unwrap();
    let restored = Store::open(&dest).unwrap();
    assert_eq!(
        restored
            .db
            .query_row("SELECT state FROM schedules", [], |r| r.get::<_, String>(0))
            .unwrap(),
        "paused"
    );
    assert_eq!(
        restored
            .db
            .query_row("SELECT enabled FROM autonomy", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        0
    );
}

#[test]
fn automation_never_restarts_a_human_decision_block() {
    let mut l = Lab::new();
    enable_contract_automation(&mut l);
    let t = l.task("Budget decision");
    l.store.tick().unwrap();
    send(&mut l.store,json!({"action":"task.block","task":t,"generation":1,"reason":"Need the owner to choose a production migration date","owner":"operator","next_check":now()+3600})).unwrap();
    l.store
        .db
        .execute("UPDATE operations SET state='confirmed'", [])
        .unwrap();
    l.store
        .db
        .execute(
            "UPDATE attempts SET progress_at=?,runtime_id='decision-worker'",
            [now() - 3600],
        )
        .unwrap();
    l.store.tick().unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":t})).unwrap()["task"]["state"],
        "blocked"
    );
    assert_eq!(
        l.store
            .db
            .query_row(
                "SELECT COUNT(*) FROM operations WHERE verb='exit'",
                [],
                |r| r.get::<_, i64>(0)
            )
            .unwrap(),
        0
    );
}

#[test]
fn automation_waiting_for_capacity_does_not_create_orphan_credentials() {
    let mut l = Lab::new();
    enable_contract_automation(&mut l);
    send(
        &mut l.store,
        json!({"action":"system.configure","global_cap":2,"project_cap":2}),
    )
    .unwrap();
    l.task("First");
    l.task("Second");
    l.store.tick().unwrap();
    let count = fs::read_dir(l.dir.path().join("credentials"))
        .unwrap()
        .count();
    for _ in 0..4 {
        l.store.tick().unwrap();
    }
    assert_eq!(
        fs::read_dir(l.dir.path().join("credentials"))
            .unwrap()
            .count(),
        count
    );
}

#[test]
fn legacy_schema_upgrade_preserves_tasks_and_rejects_unknown_future_schema() {
    let mut l = Lab::new();
    let tid = l.task("Legacy durable work");
    l.store
        .db
        .execute("UPDATE settings SET value='1' WHERE key='schema'", [])
        .unwrap();
    l.store
        .db
        .execute_batch("DROP TABLE schedule_runs; DROP TABLE schedules;")
        .unwrap();
    drop(l.store);
    l.store = Store::open(l.dir.path()).unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":tid})).unwrap()["task"]["title"],
        "Legacy durable work"
    );
    assert_eq!(
        l.store
            .db
            .query_row("SELECT value FROM settings WHERE key='schema'", [], |r| {
                r.get::<_, String>(0)
            })
            .unwrap(),
        "3"
    );
    l.store
        .db
        .execute("UPDATE settings SET value='999' WHERE key='schema'", [])
        .unwrap();
    assert!(Store::open(l.dir.path()).is_err());
}

#[test]
fn accepted_child_notifies_parent_once_and_waiting_does_not_restart_parent() {
    let mut l = Lab::new();
    enable_contract_automation(&mut l);
    let parent = l.task("Coordinate importer");
    l.claim(&parent);
    let child=send(&mut l.store,json!({"action":"child.reserve","task":parent,"generation":1,"role":"rust","title":"Bounded parser check","requirements":"Verify malformed records"})).unwrap();
    l.store
        .db
        .execute(
            "UPDATE attempts SET progress_at=?,runtime_id='parent-runtime' WHERE task=?",
            rusqlite::params![now() - 120, parent],
        )
        .unwrap();
    l.store.tick().unwrap();
    assert_eq!(
        l.store
            .db
            .query_row(
                "SELECT COUNT(*) FROM operations WHERE task=? AND verb='exit'",
                [&parent],
                |r| r.get::<_, i64>(0)
            )
            .unwrap(),
        0
    );
    let art = l.result(child["task"].as_str().unwrap(), 1);
    l.accept(&art);
    l.store.tick().unwrap();
    l.store.tick().unwrap();
    let inbox = as_agent(&mut l.store, &l.worker, json!({"action":"message.inbox"})).unwrap();
    assert_eq!(
        inbox
            .as_array()
            .unwrap()
            .iter()
            .filter(|m| m["thread"].as_str().unwrap().starts_with("dependency-"))
            .count(),
        1
    );
}

struct Lab {
    dir: TempDir,
    store: Store,
    project: String,
    worker: Value,
    reviewer: Value,
}

// These attestations exercise routing contracts only; no vendor model is run.
fn adaptive_profile(l: &mut Lab, name: &str, model: &str) {
    send(&mut l.store,json!({"action":"profile.register","profile":name,"harness":"codex","model":model,"version":"synthetic-v1","capabilities":{"roles":["rust"],"complexities":["routine","standard","complex"],"effort_by_complexity":{"routine":"low","standard":"medium","complex":"high"}}})).unwrap();
    send(&mut l.store,json!({"action":"profile.verify","profile":name,"evidence":{"harness":"codex","version":"synthetic-v1","real_harness":true,"command":"Synthetic domain fixture; not live vendor proof","observed_result":"Synthetic contract only","checks":{"launch":true,"message":true,"interrupt":true,"resume":true,"stop":true,"effort":true}}})).unwrap();
    send(
        &mut l.store,
        json!({"action":"system.configure","dispatch_enabled":true}),
    )
    .unwrap();
}

fn bind_contract_run(l: &mut Lab, tid: &str, profile: &str) {
    let operation = send(
        &mut l.store,
        json!({"action":"operation.prepare","task":tid,"profile":profile,"verb":"launch"}),
    )
    .unwrap();
    send(&mut l.store,json!({"action":"operation.receipt","operation":operation["operation"],"state":"confirmed","receipt":"Synthetic test launch, no external process"})).unwrap();
}

#[test]
fn novel_intake_creates_separate_project_and_identical_retry_deduplicates() {
    let mut l = Lab::new();
    let input = json!({"action":"intake.submit","text":"Map Himalayan trekking routes","role":"researcher"});
    let new = send(&mut l.store, input.clone()).unwrap();
    assert_eq!(new["project_created"], true);
    assert_ne!(new["project"], l.project);
    assert_eq!(send(&mut l.store, input).unwrap()["task"], new["task"]);
    assert_eq!(
        send(&mut l.store, json!({"action":"automation.status"})).unwrap(),
        json!([])
    );
    let forced=send(&mut l.store,json!({"action":"intake.submit","text":"WebCraft importer experimental variant","new_project":true,"name":"Importer experiment"})).unwrap();
    assert_ne!(forced["project"], l.project);
    assert_eq!(forced["project_created"], true);
    let matching=send(&mut l.store,json!({"action":"intake.submit","project":l.project,"text":"WebCraft preserve the importer rows"})).unwrap();
    assert_eq!(matching["project"], l.project);
    assert_eq!(matching["project_created"], false);
}

#[test]
fn intake_does_not_match_short_names_inside_unrelated_words() {
    let mut l = Lab::new();
    send(
        &mut l.store,
        json!({"action":"project.create","name":"Art","charter":"Gallery paintings"}),
    )
    .unwrap();
    let result = send(
        &mut l.store,
        json!({"action":"intake.preview","text":"Repair shopping cart checkout"}),
    )
    .unwrap();
    assert_eq!(result["route"], "new_project");
}

#[test]
fn explicit_intake_amendment_preserves_history_and_notifies_current_owner_once() {
    let mut l = Lab::new();
    let tid = l.task("Importer");
    l.claim(&tid);
    let out=send(&mut l.store,json!({"action":"intake.submit","task":tid,"expected_version":1,"text":"Retain invalid CSV rows and emit diagnostics"})).unwrap();
    assert_eq!(out["amended"], true);
    assert_eq!(out["version"], 2);
    l.store.tick().unwrap();
    l.store.tick().unwrap();
    let inbox = as_agent(&mut l.store, &l.worker, json!({"action":"message.inbox"})).unwrap();
    assert_eq!(inbox.as_array().unwrap().len(), 1);
    assert_eq!(inbox[0]["version"], 2);
    assert!(send(&mut l.store,json!({"action":"task.result","task":tid,"generation":1,"body":"old result","code_revision":"old"})).is_err());
    assert!(send(
        &mut l.store,
        json!({"action":"intake.submit","task":tid,"expected_version":1,"text":"stale overwrite"})
    )
    .is_err());
    as_agent(&mut l.store,&l.worker,json!({"action":"task.ack_version","task":tid,"version":2,"checkpoint":"Reviewed changed requirements"})).unwrap();
    let detail = send(&mut l.store, json!({"action":"task.get","task":tid})).unwrap();
    assert_eq!(detail["versions"].as_array().unwrap().len(), 2);
    assert_eq!(
        detail["task"]["requirements"],
        "Retain invalid CSV rows and emit diagnostics"
    );
}

#[test]
fn requirement_change_survives_a_full_mailbox_and_retries_after_backpressure() {
    let mut l = Lab::new();
    let tid = l.task("Importer");
    l.claim(&tid);
    let mut first = Value::Null;
    for n in 0..160 {
        let message=send(&mut l.store,json!({"action":"message.send","recipient":l.worker["agent"],"thread":format!("pressure-{n}"),"priority":"critical","body":"Outstanding evidence"})).unwrap();
        if n == 0 {
            first = message["message"].clone();
        }
    }
    send(&mut l.store,json!({"action":"task.amend","task":tid,"expected_version":1,"requirements":"Updated contract despite full inbox"})).unwrap();
    l.store.tick().unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":tid})).unwrap()["task"]["version"],
        2
    );
    for state in ["delivered", "read", "acted"] {
        as_agent(
            &mut l.store,
            &l.worker,
            json!({"action":"message.ack","message":first,"state":state}),
        )
        .unwrap();
    }
    l.store.tick().unwrap();
    let notices = send(&mut l.store, json!({"action":"task.get","task":tid})).unwrap()
        ["notifications"]
        .clone();
    let message = as_agent(
        &mut l.store,
        &l.worker,
        json!({"action":"message.get","message":notices[0]["message"]}),
    )
    .unwrap();
    assert_eq!(message["version"], 2);
}

#[test]
fn complexity_uses_verified_effort_mapping_and_legacy_profiles_cannot_claim_complex_work() {
    let mut l = Lab::new();
    enable_contract_automation(&mut l);
    let tid=send(&mut l.store,json!({"action":"task.create","project":l.project,"title":"Distributed architecture migration","requirements":"Preserve records","role":"rust"})).unwrap()["task"].clone();
    assert!(
        send(&mut l.store, json!({"action":"dispatch.plan","task":tid})).unwrap()["recommended"]
            .is_null()
    );
    adaptive_profile(&mut l, "deep", "fixture-deep");
    let plan = send(&mut l.store, json!({"action":"dispatch.plan","task":tid})).unwrap();
    assert_eq!(plan["classification"]["complexity"], "complex");
    assert_eq!(plan["recommended"]["effort"], "high");
    assert_eq!(plan["recommended"]["profile"], "deep");
    assert_eq!(
        plan["recommended"]["confidence"],
        "insufficient local evidence"
    );
}

#[test]
fn rejected_reviews_learn_once_and_change_recovery_profile_without_changing_live_control() {
    let mut l = Lab::new();
    adaptive_profile(&mut l, "a-fast", "fixture-fast");
    adaptive_profile(&mut l, "b-deep", "fixture-deep");
    let tid = l.task("Importer");
    l.claim(&tid);
    bind_contract_run(&mut l, &tid, "a-fast");
    for _ in 0..2 {
        let art = l.result(&tid, 1);
        send(&mut l.store,json!({"action":"task.review","artifact":art["artifact"],"reviewer":l.reviewer["agent"],"passed":false,"code_revision":"abc123","evidence":"Invalid rows are still lost; reproduce with fixture rows 3 and 9"})).unwrap();
    }
    let plan = send(&mut l.store, json!({"action":"dispatch.plan","task":tid})).unwrap();
    assert_eq!(plan["recommended"]["profile"], "b-deep");
    assert_eq!(plan["recommended"]["effort"], "high");
    assert!(send(
        &mut l.store,
        json!({"action":"operation.prepare","task":tid,"profile":"b-deep","verb":"exit"})
    )
    .is_err());
    let exit = send(
        &mut l.store,
        json!({"action":"operation.prepare","task":tid,"profile":"a-fast","verb":"exit"}),
    )
    .unwrap();
    send(&mut l.store,json!({"action":"operation.receipt","operation":exit["operation"],"state":"confirmed","receipt":"Synthetic stop evidence"})).unwrap();
    send(&mut l.store,json!({"action":"task.recover","task":tid,"confirmed_stopped":true,"evidence":"Synthetic test stopped; no real vendor run"})).unwrap();
    l.claim(&tid);
    bind_contract_run(&mut l, &tid, "b-deep");
    let art = l.result(&tid, 2);
    l.accept(&art);
    let learned = send(
        &mut l.store,
        json!({"action":"learning.status","project":l.project}),
    )
    .unwrap();
    assert_eq!(learned["lessons"].as_array().unwrap().len(), 3);
    assert_eq!(learned["outcomes"].as_array().unwrap().len(), 2);
    assert!(learned["outcomes"]
        .as_array()
        .unwrap()
        .iter()
        .any(|o| o["profile"] == "a-fast" && o["failures"] == 2));
    drop(l.store);
    l.store = Store::open(l.dir.path()).unwrap();
    let ctx = send(
        &mut l.store,
        json!({"action":"project.context","project":l.project}),
    )
    .unwrap();
    assert_eq!(ctx["learning"]["lessons"].as_array().unwrap().len(), 3);
}

#[test]
fn automatic_quality_ranking_needs_five_matching_runs_and_resets_for_new_model() {
    let mut l = Lab::new();
    adaptive_profile(&mut l, "a-no-evidence", "fixture-a");
    adaptive_profile(&mut l, "z-proven", "fixture-z");
    for n in 0..5 {
        let tid = l.task(&format!("Import batch {n}"));
        l.claim(&tid);
        bind_contract_run(&mut l, &tid, "z-proven");
        let art = l.result(&tid, 1);
        l.accept(&art);
        let probe = l.task(&format!("Next batch {n}"));
        let plan = send(&mut l.store, json!({"action":"dispatch.plan","task":probe})).unwrap();
        assert_eq!(
            plan["recommended"]["profile"],
            if n < 4 { "a-no-evidence" } else { "z-proven" }
        );
    }
    let tid = l.task("Final batch");
    adaptive_profile(&mut l, "z-proven", "fixture-z-new-model");
    let plan = send(&mut l.store, json!({"action":"dispatch.plan","task":tid})).unwrap();
    assert_eq!(plan["recommended"]["profile"], "a-no-evidence");
    assert_eq!(plan["candidates"][1]["evidence"]["samples"], 0);
}

#[test]
fn automatic_failed_review_stops_old_profile_before_switching_to_verified_alternative() {
    let mut l = Lab::new();
    enable_contract_automation(&mut l);
    adaptive_profile(&mut l, "a-fast", "fixture-fast");
    adaptive_profile(&mut l, "b-deep", "fixture-deep");
    let tid = l.task("Import evidence");
    l.store.tick().unwrap();
    // Synthetic runtime observations and receipts. No vendor model is launched.
    let settle = |l: &mut Lab| {
        let pending = send(&mut l.store, json!({"action":"operations.list"})).unwrap();
        for op in pending
            .as_array()
            .unwrap()
            .iter()
            .filter(|o| o["state"] == "pending")
        {
            send(&mut l.store,json!({"action":"operation.receipt","operation":op["id"],"state":"confirmed","receipt":"Synthetic lifecycle contract"})).unwrap();
        }
    };
    settle(&mut l);
    l.store.db.execute("UPDATE attempts SET runtime_id='quality-runtime',runtime_generation='quality-g1' WHERE task=?",[&tid]).unwrap();
    for _ in 0..2 {
        l.result(&tid, 1);
        l.store.tick().unwrap();
        settle(&mut l);
        let all = send(&mut l.store, json!({"action":"snapshot"})).unwrap();
        let review = all["tasks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["role"] == "quality" && t["state"] == "running")
            .unwrap();
        let reviewer = json!({"agent":review["owner"],"credential_file":l.dir.path().join("credentials").join(format!("{}.token",review["owner"].as_str().unwrap()))});
        as_agent(&mut l.store,&reviewer,json!({"action":"review.submit","task":review["id"],"generation":1,"passed":false,"code_revision":"abc123","evidence":"Fixture loses row 3; changed implementation needed"})).unwrap();
        l.store.tick().unwrap();
        settle(&mut l);
    }
    let state = send(&mut l.store, json!({"action":"task.get","task":tid})).unwrap();
    assert_eq!(state["task"]["state"], "recovering");
    assert_eq!(state["task"]["generation"], 1);
    l.store.tick().unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":tid})).unwrap()["task"]["generation"],
        1
    );
    let ops = send(&mut l.store, json!({"action":"operations.list"})).unwrap();
    let stop = ops
        .as_array()
        .unwrap()
        .iter()
        .find(|o| o["task"] == tid && o["verb"] == "exit")
        .unwrap();
    let body: Value = serde_json::from_str(stop["body"].as_str().unwrap()).unwrap();
    assert_eq!(body["profile"], "a-fast");
    let stamp = chrono::DateTime::from_timestamp(now(), 0)
        .unwrap()
        .to_rfc3339();
    send(&mut l.store,json!({"action":"runtime.observe","snapshot":{"schema":"fm-fleet-snapshot.v1","tasks":[{"id":"quality-runtime","spawn_gen":"quality-g1","remote":null,"current_state":{"state":"unknown","freshness":"fresh","observed_at":stamp},"endpoint":{"exists":false,"agent_alive":"dead","freshness":"fresh","observed_at":stamp}}]}})).unwrap();
    l.store.tick().unwrap();
    let state = send(&mut l.store, json!({"action":"task.get","task":tid})).unwrap();
    assert_eq!(state["task"]["generation"], 2);
    assert_eq!(state["task"]["replans"], 0);
    let ops = send(&mut l.store, json!({"action":"operations.list"})).unwrap();
    let launch = ops
        .as_array()
        .unwrap()
        .iter()
        .find(|o| o["task"] == tid && o["state"] == "pending" && o["verb"] == "launch")
        .unwrap();
    let body: Value = serde_json::from_str(launch["body"].as_str().unwrap()).unwrap();
    assert_eq!(body["profile"], "b-deep");
    assert_eq!(body["effort"], "high");
}

#[test]
fn changed_requirements_retain_old_lessons_without_penalizing_the_new_contract() {
    let mut l = Lab::new();
    adaptive_profile(&mut l, "only-profile", "fixture-model");
    let tid = l.task("Importer");
    l.claim(&tid);
    bind_contract_run(&mut l, &tid, "only-profile");
    for _ in 0..2 {
        let art = l.result(&tid, 1);
        send(&mut l.store,json!({"action":"task.review","artifact":art["artifact"],"reviewer":l.reviewer["agent"],"passed":false,"code_revision":"abc123","evidence":"Original acceptance condition is not satisfied"})).unwrap();
    }
    assert!(
        send(&mut l.store, json!({"action":"dispatch.plan","task":tid})).unwrap()["recommended"]
            .is_null()
    );
    send(&mut l.store,json!({"action":"task.amend","task":tid,"expected_version":1,"requirements":"A different explicitly agreed contract","complexity":"standard"})).unwrap();
    let plan = send(&mut l.store, json!({"action":"dispatch.plan","task":tid})).unwrap();
    assert_eq!(plan["recommended"]["profile"], "only-profile");
    assert_eq!(plan["classification"]["failure_records"], 0);
    let detail = send(&mut l.store, json!({"action":"task.get","task":tid})).unwrap();
    assert_eq!(detail["learning"].as_array().unwrap().len(), 2);
    assert_eq!(detail["learning"][0]["version"], 1);
}

#[test]
fn acknowledging_requirements_cannot_clear_an_unrelated_human_blocker() {
    let mut l = Lab::new();
    let tid = l.task("Blocked importer");
    l.claim(&tid);
    assert!(as_agent(
        &mut l.store,
        &l.worker,
        json!({"action":"task.ack_version","task":tid,"version":1,"checkpoint":"No amendment"})
    )
    .is_err());
    as_agent(&mut l.store,&l.worker,json!({"action":"task.block","task":tid,"generation":1,"reason":"Operator must choose data retention policy","owner":"operator","next_check":now()+600})).unwrap();
    send(&mut l.store,json!({"action":"task.amend","task":tid,"expected_version":1,"requirements":"Also emit a checksum"})).unwrap();
    let ack=as_agent(&mut l.store,&l.worker,json!({"action":"task.ack_version","task":tid,"version":2,"checkpoint":"Checksum requirement understood"})).unwrap();
    assert_eq!(ack["state"], "blocked");
    let detail = send(&mut l.store, json!({"action":"task.get","task":tid})).unwrap();
    assert_eq!(detail["task"]["state"], "blocked");
    assert_eq!(
        detail["task"]["reason"],
        "Operator must choose data retention policy"
    );
}

#[test]
fn scheduled_and_child_tasks_use_the_same_complexity_policy() {
    let mut l = Lab::new();
    let parent = l.task("Parent");
    l.claim(&parent);
    let child=as_agent(&mut l.store,&l.worker,json!({"action":"child.reserve","task":parent,"generation":1,"title":"Concurrency audit","requirements":"Find distributed deadlock risks","role":"rust"})).unwrap();
    let child_plan = send(
        &mut l.store,
        json!({"action":"dispatch.plan","task":child["task"]}),
    )
    .unwrap();
    assert_eq!(child_plan["classification"]["complexity"], "complex");
    let schedule=send(&mut l.store,json!({"action":"schedule.create","project":l.project,"title":"Weekly security review","requirements":"Review authentication changes","role":"rust","cadence":"weekly"})).unwrap();
    l.store
        .db
        .execute(
            "UPDATE schedules SET next_run=? WHERE id=?",
            rusqlite::params![now() - 1, schedule["schedule"].as_str()],
        )
        .unwrap();
    l.store.tick().unwrap();
    let agenda = send(&mut l.store, json!({"action":"agenda","view":"today"})).unwrap();
    let job = agenda["jobs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|t| t["schedule"] == schedule["schedule"])
        .unwrap();
    assert_eq!(
        send(
            &mut l.store,
            json!({"action":"dispatch.plan","task":job["id"]})
        )
        .unwrap()["classification"]["complexity"],
        "complex"
    );
}
fn send(store: &mut Store, mut req: Value) -> anyhow::Result<Value> {
    if req.get("request_id").is_none() {
        req["request_id"] = json!(id("test"));
    }
    store.execute(&Principal::operator(), &req)
}
fn as_agent(store: &mut Store, agent: &Value, mut req: Value) -> anyhow::Result<Value> {
    req["request_id"] = json!(id("agent"));
    let token = fs::read_to_string(agent["credential_file"].as_str().unwrap())?;
    let actor = store.authenticate(&token)?;
    store.execute(&actor, &req)
}
impl Lab {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let mut store = Store::open(dir.path()).unwrap();
        let p=send(&mut store,json!({"action":"project.create","name":"WebCraft","charter":"Deliver a verified importer","aliases":["webcraft"]})).unwrap();
        let project = p["project"].as_str().unwrap().to_string();
        let worker = send(
            &mut store,
            json!({"action":"agent.create","project":project,"role":"rust","harness":"codex"}),
        )
        .unwrap();
        let reviewer = send(
            &mut store,
            json!({"action":"agent.create","project":project,"role":"quality","harness":"claude"}),
        )
        .unwrap();
        Self {
            dir,
            store,
            project,
            worker,
            reviewer,
        }
    }
    fn task(&mut self, title: &str) -> String {
        send(&mut self.store,json!({"action":"task.create","project":self.project,"title":title,"requirements":"Preserve data and prove current acceptance criteria","role":"rust"})).unwrap()["task"].as_str().unwrap().into()
    }
    fn claim(&mut self, t: &str) -> Value {
        send(
            &mut self.store,
            json!({"action":"task.claim","task":t,"agent":self.worker["agent"]}),
        )
        .unwrap()
    }
    fn result(&mut self, t: &str, g: i64) -> Value {
        send(&mut self.store,json!({"action":"task.result","task":t,"generation":g,"body":"A full result with evidence and artifacts","code_revision":"abc123"})).unwrap()
    }
    fn accept(&mut self, a: &Value) -> Value {
        send(&mut self.store,json!({"action":"task.review","artifact":a["artifact"],"reviewer":self.reviewer["agent"],"code_revision":"abc123","passed":true,"evidence":"Unit, integration and acceptance checks passed for abc123."})).unwrap()
    }
}

#[test]
fn durable_receipt_replays_after_restart_without_duplicate_task() {
    let mut l = Lab::new();
    let req = json!({"action":"task.create","project":l.project,"title":"Import","requirements":"No lost records","role":"rust","request_id":"fixed"});
    let first = send(&mut l.store, req.clone()).unwrap();
    drop(l.store);
    let mut store = Store::open(l.dir.path()).unwrap();
    assert_eq!(first, send(&mut store, req).unwrap());
    assert_eq!(
        send(&mut store, json!({"action":"snapshot"})).unwrap()["tasks"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}
#[test]
fn reused_request_id_with_changed_body_is_rejected() {
    let mut l = Lab::new();
    let mut req = json!({"action":"task.create","project":l.project,"title":"A","requirements":"A","request_id":"same"});
    send(&mut l.store, req.clone()).unwrap();
    req["title"] = json!("B");
    assert!(send(&mut l.store, req)
        .unwrap_err()
        .to_string()
        .contains("different content"));
}
#[test]
fn amendments_reject_stale_output_and_stale_review() {
    let mut l = Lab::new();
    let t = l.task("Importer");
    l.claim(&t);
    let old = l.result(&t, 1);
    send(&mut l.store,json!({"action":"task.amend","task":t,"expected_version":1,"requirements":"Also preserve invalid records"})).unwrap();
    assert!(send(&mut l.store,json!({"action":"task.result","task":t,"generation":1,"body":"old","code_revision":"abc123"})).is_err());
    assert!(send(&mut l.store,json!({"action":"task.review","artifact":old["artifact"],"reviewer":l.reviewer["agent"],"passed":true,"code_revision":"abc123","evidence":"old"})).is_err());
    send(
        &mut l.store,
        json!({"action":"task.ack_version","task":t,"version":2,"checkpoint":"old process safe"}),
    )
    .unwrap();
    let a = l.result(&t, 1);
    assert_eq!(l.accept(&a)["state"], "accepted");
}
#[test]
fn self_review_and_wrong_code_revision_are_rejected() {
    let mut l = Lab::new();
    let t = l.task("Audit");
    l.claim(&t);
    let a = l.result(&t, 1);
    assert!(send(&mut l.store,json!({"action":"task.review","artifact":a["artifact"],"reviewer":l.worker["agent"],"passed":true,"code_revision":"abc123","evidence":"self"})).is_err());
    assert!(send(&mut l.store,json!({"action":"task.review","artifact":a["artifact"],"reviewer":l.reviewer["agent"],"passed":true,"code_revision":"different","evidence":"wrong code"})).is_err());
}
#[test]
fn admission_is_atomic_under_concurrent_claims() {
    let mut l = Lab::new();
    send(
        &mut l.store,
        json!({"action":"system.configure","global_cap":1,"project_cap":1}),
    )
    .unwrap();
    let tasks = (0..8)
        .map(|i| l.task(&format!("Task {i}")))
        .collect::<Vec<_>>();
    let barrier = Arc::new(Barrier::new(8));
    let home = l.dir.path().to_path_buf();
    let agent = l.worker["agent"].as_str().unwrap().to_string();
    let handles = tasks
        .into_iter()
        .map(|t| {
            let barrier = barrier.clone();
            let home = home.clone();
            let agent = agent.clone();
            thread::spawn(move || {
                let mut s = Store::open(&home).unwrap();
                barrier.wait();
                send(
                    &mut s,
                    json!({"action":"task.claim","task":t,"agent":agent}),
                )
                .is_ok()
            })
        })
        .collect::<Vec<_>>();
    assert_eq!(
        handles
            .into_iter()
            .filter_map(|h| h.join().ok())
            .filter(|x| *x)
            .count(),
        1
    );
}
#[test]
fn project_scope_applies_to_reads_and_writes() {
    let mut l = Lab::new();
    let p = send(
        &mut l.store,
        json!({"action":"project.create","name":"Other","charter":"Separate scope"}),
    )
    .unwrap();
    let t=send(&mut l.store,json!({"action":"task.create","project":p["project"],"title":"Private","requirements":"Not shared"})).unwrap();
    assert!(as_agent(
        &mut l.store,
        &l.worker,
        json!({"action":"task.get","task":t["task"]})
    )
    .is_err());
    assert!(as_agent(
        &mut l.store,
        &l.worker,
        json!({"action":"system.configure","global_cap":100})
    )
    .is_err());
    let list = as_agent(&mut l.store, &l.worker, json!({"action":"projects.list"})).unwrap();
    assert_eq!(list.as_array().unwrap().len(), 1);
}
#[test]
fn dependency_cycles_are_rejected_and_ready_queue_respects_acceptance() {
    let mut l = Lab::new();
    let a = l.task("A");
    let b = l.task("B");
    send(
        &mut l.store,
        json!({"action":"task.link","task":a,"depends_on":b}),
    )
    .unwrap();
    assert!(send(
        &mut l.store,
        json!({"action":"task.link","task":b,"depends_on":a})
    )
    .is_err());
    assert!(send(
        &mut l.store,
        json!({"action":"task.claim","task":a,"agent":l.worker["agent"]})
    )
    .is_err());
    let next = send(&mut l.store, json!({"action":"task.next"})).unwrap();
    assert_eq!(next[0]["id"], b);
}
#[test]
fn messages_have_distinct_delivery_read_and_action_receipts() {
    let mut l = Lab::new();
    let t = l.task("Message");
    let m=as_agent(&mut l.store,&l.worker,json!({"action":"message.send","recipient":l.reviewer["agent"],"task":t,"thread":"review","body":"Please inspect the contract"})).unwrap();
    assert_eq!(m["state"], "persisted");
    assert!(as_agent(
        &mut l.store,
        &l.reviewer,
        json!({"action":"message.ack","message":m["message"],"state":"acted"})
    )
    .is_err());
    for state in ["delivered", "read", "acted"] {
        as_agent(
            &mut l.store,
            &l.reviewer,
            json!({"action":"message.ack","message":m["message"],"state":state}),
        )
        .unwrap();
    }
    assert_eq!(
        as_agent(&mut l.store, &l.reviewer, json!({"action":"message.inbox"})).unwrap(),
        json!([])
    );
}
#[test]
fn stale_version_messages_cannot_be_acknowledged_as_current() {
    let mut l = Lab::new();
    let t = l.task("Message");
    let m=as_agent(&mut l.store,&l.worker,json!({"action":"message.send","recipient":l.reviewer["agent"],"task":t,"thread":"review","body":"Version one"})).unwrap();
    send(
        &mut l.store,
        json!({"action":"task.amend","task":t,"expected_version":1,"requirements":"Version two"}),
    )
    .unwrap();
    assert!(as_agent(
        &mut l.store,
        &l.reviewer,
        json!({"action":"message.ack","message":m["message"],"state":"delivered"})
    )
    .is_err());
}
#[test]
fn mailboxes_apply_backpressure_and_reserve_critical_capacity() {
    let mut l = Lab::new();
    for i in 0..128 {
        as_agent(&mut l.store,&l.worker,json!({"action":"message.send","recipient":l.reviewer["agent"],"thread":format!("thread-{i}"),"body":"normal"})).unwrap();
    }
    assert!(as_agent(&mut l.store,&l.worker,json!({"action":"message.send","recipient":l.reviewer["agent"],"thread":"overflow","body":"normal"})).is_err());
    let urgent=as_agent(&mut l.store,&l.worker,json!({"action":"message.send","recipient":l.reviewer["agent"],"thread":"incident","body":"critical","priority":"critical"})).unwrap();
    assert_eq!(urgent["state"], "persisted");
}
#[test]
fn context_transfer_requires_all_durable_records_and_revokes_old_generation() {
    let mut l = Lab::new();
    adaptive_profile(&mut l, "context-profile", "fixture-model");
    let t = l.task("Context");
    l.claim(&t);
    bind_contract_run(&mut l, &t, "context-profile");
    let art = l.result(&t, 1);
    send(&mut l.store,json!({"action":"task.review","artifact":art["artifact"],"reviewer":l.reviewer["agent"],"code_revision":"abc123","passed":false,"evidence":"Retain the discovered missing-row regression during handover"})).unwrap();
    send(
        &mut l.store,
        json!({"action":"task.pause","task":t,"reason":"Verified safe checkpoint"}),
    )
    .unwrap();
    let cap=send(&mut l.store,json!({"action":"context.create","task":t,"generation":1,"checkpoint":"code abc123, no open effects"})).unwrap();
    for prefix in ["lesson:", "routing:", "profile:", "outcome:"] {
        assert!(cap["required"]
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r["key"].as_str().unwrap().starts_with(prefix)));
    }
    let next = send(
        &mut l.store,
        json!({"action":"agent.create","project":l.project,"role":"rust","harness":"claude"}),
    )
    .unwrap();
    assert!(send(&mut l.store,json!({"action":"context.verify","capsule":cap["capsule"],"successor":next["agent"],"reconstructed":[],"next_action":"Continue"})).is_err());
    send(&mut l.store,json!({"action":"context.verify","capsule":cap["capsule"],"successor":next["agent"],"reconstructed":cap["required"],"next_action":"Continue from abc123"})).unwrap();
    assert!(send(
        &mut l.store,
        json!({"action":"context.transfer","capsule":cap["capsule"]})
    )
    .is_err());
    let transfer = send(
        &mut l.store,
        json!({"action":"context.transfer","capsule":cap["capsule"],"confirmed_stopped":true}),
    )
    .unwrap();
    assert_eq!(transfer["generation"], 2);
    assert!(send(
        &mut l.store,
        json!({"action":"task.heartbeat","task":t,"generation":1})
    )
    .is_err());
}
#[test]
fn new_information_invalidates_a_context_manifest() {
    let mut l = Lab::new();
    let t = l.task("Context");
    l.claim(&t);
    send(
        &mut l.store,
        json!({"action":"task.pause","task":t,"reason":"Checkpoint"}),
    )
    .unwrap();
    let cap = send(
        &mut l.store,
        json!({"action":"context.create","task":t,"generation":1,"checkpoint":"safe"}),
    )
    .unwrap();
    send(&mut l.store,json!({"action":"message.send","recipient":l.worker["agent"],"task":t,"thread":"new","body":"New evidence"})).unwrap();
    let next = send(
        &mut l.store,
        json!({"action":"agent.create","project":l.project,"role":"rust"}),
    )
    .unwrap();
    assert!(send(&mut l.store,json!({"action":"context.verify","capsule":cap["capsule"],"successor":next["agent"],"reconstructed":cap["required"],"next_action":"Continue"})).is_err());
}
#[test]
fn stale_contact_becomes_unknown_without_releasing_ownership() {
    let mut l = Lab::new();
    let t = l.task("Stale");
    l.claim(&t);
    l.store
        .db
        .execute("UPDATE attempts SET heartbeat=?", [now() - 40])
        .unwrap();
    l.store.tick().unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":t})).unwrap()["task"]["state"],
        "unknown"
    );
    assert!(send(
        &mut l.store,
        json!({"action":"task.recover","task":t,"evidence":"maybe dead"})
    )
    .is_err());
    as_agent(
        &mut l.store,
        &l.worker,
        json!({"action":"task.heartbeat","task":t,"generation":1}),
    )
    .unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"task.get","task":t})).unwrap()["task"]["state"],
        "running"
    );
}
#[test]
fn recovery_is_bounded_and_late_attempts_are_fenced() {
    let mut l = Lab::new();
    let t = l.task("Recover");
    l.claim(&t);
    for generation in 1..=2 {
        send(&mut l.store,json!({"action":"task.recover","task":t,"confirmed_stopped":true,"evidence":"Adapter confirmed stopped; tool effects reconciled"})).unwrap();
        l.claim(&t);
        assert!(send(
            &mut l.store,
            json!({"action":"task.heartbeat","task":t,"generation":generation})
        )
        .is_err());
    }
    let exhausted=send(&mut l.store,json!({"action":"task.recover","task":t,"confirmed_stopped":true,"evidence":"Stopped again"})).unwrap();
    assert_eq!(exhausted["reason"], "recovery_budget_exhausted");
}
#[test]
fn repeated_nonprogress_allows_one_replan_then_escalates() {
    let mut l = Lab::new();
    let t = l.task("Loop");
    l.claim(&t);
    for round in 0..2 {
        for _ in 0..4 {
            let _ = send(
                &mut l.store,
                json!({"action":"task.progress","task":t,"generation":1,"fingerprint":"same-failure"}),
            );
        }
        let resume = send(
            &mut l.store,
            json!({"action":"task.resume","task":t,"reason":"Change the hypothesis"}),
        );
        if round == 0 {
            resume.unwrap();
        } else {
            assert!(resume.is_err());
        }
    }
}
#[test]
fn heartbeats_are_not_false_progress_or_loop_signals() {
    let mut l = Lab::new();
    let t = l.task("Long compiler");
    l.claim(&t);
    for _ in 0..8 {
        send(
            &mut l.store,
            json!({"action":"task.heartbeat","task":t,"generation":1}),
        )
        .unwrap();
    }
    let v = send(&mut l.store, json!({"action":"task.get","task":t})).unwrap();
    assert_eq!(v["task"]["state"], "running");
    assert_eq!(v["attempts"][0]["repeats"], 0);
}
#[test]
fn children_reserve_capacity_and_parent_cannot_finish_early() {
    let mut l = Lab::new();
    let t = l.task("Parent");
    l.claim(&t);
    let child=as_agent(&mut l.store,&l.worker,json!({"action":"child.reserve","task":t,"generation":1,"role":"researcher","title":"Bounded child","requirements":"Return a source comparison"})).unwrap();
    assert!(child["reservation"]["attempt"].is_string());
    assert!(as_agent(&mut l.store,&l.worker,json!({"action":"child.reserve","task":t,"generation":1,"role":"researcher","title":"Overflow","requirements":"No capacity"})).is_err());
    assert!(send(&mut l.store,json!({"action":"task.result","task":t,"generation":1,"body":"premature","code_revision":"abc123"})).is_err());
    let state = send(&mut l.store, json!({"action":"snapshot"})).unwrap();
    assert_eq!(state["tasks"].as_array().unwrap().len(), 2);
}
#[test]
fn backup_restore_disables_dispatch_and_revokes_credentials() {
    let mut l = Lab::new();
    let t = l.task("Persist");
    l.claim(&t);
    let token = fs::read_to_string(l.worker["credential_file"].as_str().unwrap()).unwrap();
    let area = tempfile::tempdir().unwrap();
    let copy = area.path().join("backup");
    backup::create(&l.store, &copy).unwrap();
    let dest = area.path().join("restore");
    backup::restore(&copy, &dest).unwrap();
    let mut restored = Store::open(&dest).unwrap();
    assert!(restored.authenticate(&token).is_err());
    let task = send(&mut restored, json!({"action":"task.get","task":t})).unwrap();
    assert_eq!(task["task"]["state"], "unknown");
    assert!(backup::restore(&copy, &dest).is_err());
}
#[test]
fn corrupt_backups_fail_verification() {
    let l = Lab::new();
    let area = tempfile::tempdir().unwrap();
    let copy = area.path().join("backup");
    backup::create(&l.store, &copy).unwrap();
    fs::write(copy.join("olympus.db"), b"corrupt").unwrap();
    assert!(backup::verify(&copy).is_err());
}
#[test]
fn chief_cannot_report_idle_while_ready_work_is_ignored() {
    let mut l = Lab::new();
    l.task("Ready");
    let chief = send(
        &mut l.store,
        json!({"action":"agent.create","role":"chief"}),
    )
    .unwrap();
    assert!(as_agent(
        &mut l.store,
        &chief,
        json!({"action":"chief.finish","disposition":"idle","reason":"waste of time"})
    )
    .is_err());
    assert!(as_agent(&mut l.store,&chief,json!({"action":"chief.finish","disposition":"waiting","reason":"quota","next_check":now()+60})).is_ok());
    assert!(as_agent(&mut l.store, &chief, json!({"action":"system.shutdown"})).is_err());
}
#[test]
fn simulated_evidence_cannot_enable_real_harness_profiles() {
    let mut l = Lab::new();
    send(&mut l.store,json!({"action":"profile.register","profile":"codex-verified","harness":"codex","model":"configured","version":"test-version"})).unwrap();
    assert!(send(&mut l.store,json!({"action":"profile.verify","profile":"codex-verified","evidence":{"harness":"codex","version":"test-version","command":"fixture","observed_result":"fixture","real_harness":false,"checks":{"launch":true,"message":true,"interrupt":true,"resume":true,"stop":true}}})).is_err());
}
#[test]
fn credentials_never_enter_event_or_receipt_bodies() {
    let l = Lab::new();
    let token = fs::read_to_string(l.worker["credential_file"].as_str().unwrap()).unwrap();
    let mut query = l
        .store
        .db
        .prepare("SELECT body FROM events UNION ALL SELECT result FROM receipts")
        .unwrap();
    let values = query
        .query_map([], |r| r.get::<_, String>(0))
        .unwrap()
        .map(Result::unwrap)
        .collect::<Vec<_>>();
    assert!(!values.join("\n").contains(&token));
    use std::os::unix::fs::PermissionsExt;
    assert_eq!(
        fs::metadata(l.worker["credential_file"].as_str().unwrap())
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
}
#[test]
fn histories_page_without_duplicates_and_keep_full_bodies() {
    let mut l = Lab::new();
    for i in 0..12 {
        let t = l.task(&format!("Task {i}"));
        l.claim(&t);
        let a = l.result(&t, 1);
        l.accept(&a);
    }
    let first = send(
        &mut l.store,
        json!({"action":"history","kind":"tasks","limit":10}),
    )
    .unwrap();
    let second = send(
        &mut l.store,
        json!({"action":"history","kind":"tasks","limit":10,"before":first[9]["cursor"]}),
    )
    .unwrap();
    assert_eq!(first.as_array().unwrap().len(), 10);
    assert_eq!(second.as_array().unwrap().len(), 2);
    assert_eq!(first[0]["title"], "Task 11");
    let responses = send(&mut l.store, json!({"action":"history","limit":100})).unwrap();
    assert_eq!(responses.as_array().unwrap().len(), 24);
    assert!(responses[1]["body"]
        .as_str()
        .unwrap()
        .contains("full result"));
}
#[test]
fn demo_exercises_twenty_projects_without_model_calls() {
    let dir = tempfile::tempdir().unwrap();
    let mut s = Store::open(dir.path()).unwrap();
    let result = demo::seed(&mut s).unwrap();
    assert_eq!(result["projects"], 20);
    assert_eq!(result["model_calls"], 0);
    let snap = send(&mut s, json!({"action":"snapshot"})).unwrap();
    assert_eq!(snap["responses"].as_array().unwrap().len(), 5);
    assert_eq!(snap["completed"].as_array().unwrap().len(), 10);
    assert!(snap["counts"]["fresh_active"].as_i64().unwrap() > 0);
    assert_eq!(snap["chief"], "idle");
}
#[test]
fn intake_is_explainable_and_never_mutates_from_similarity() {
    let mut l = Lab::new();
    let result = send(
        &mut l.store,
        json!({"action":"intake.preview","text":"Fix the webcraft importer"}),
    )
    .unwrap();
    assert_eq!(result["route"], "existing_project");
    assert_eq!(result["mutated"], false);
    assert!(
        send(&mut l.store, json!({"action":"snapshot"})).unwrap()["tasks"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}
#[test]
fn second_brain_preserves_provenance_and_superseded_records() {
    let mut l = Lab::new();
    assert!(send(
        &mut l.store,
        json!({"action":"memory.put","project":l.project,"title":"Fact","body":"No evidence"})
    )
    .is_err());
    let old=send(&mut l.store,json!({"action":"memory.put","project":l.project,"title":"Decision","body":"Version one","sources":["decision-1"],"status":"verified"})).unwrap();
    let new=send(&mut l.store,json!({"action":"memory.put","project":l.project,"title":"Decision","body":"Version two","sources":["decision-2"],"status":"verified"})).unwrap();
    send(
        &mut l.store,
        json!({"action":"memory.supersede","old":old["knowledge"],"new":new["knowledge"]}),
    )
    .unwrap();
    let found = send(
        &mut l.store,
        json!({"action":"memory.search","query":"Decision"}),
    )
    .unwrap();
    assert_eq!(found.as_array().unwrap().len(), 1);
    assert_eq!(found[0]["body"], "Version two");
}

#[test]
fn context_inventory_fetches_exact_records_without_repeating_the_entire_history() {
    let mut l = Lab::new();
    let task = l.task("Rollover");
    l.claim(&task);
    send(
        &mut l.store,
        json!({"action":"task.pause","task":task,"reason":"Safe checkpoint"}),
    )
    .unwrap();
    let cap = send(
        &mut l.store,
        json!({"action":"context.create","task":task,"generation":1,"checkpoint":"checked"}),
    )
    .unwrap();
    let inventory = send(
        &mut l.store,
        json!({"action":"context.get","capsule":cap["capsule"]}),
    )
    .unwrap();
    assert!(inventory["manifest"][0].get("data").is_none());
    let record=send(&mut l.store,json!({"action":"context.get","capsule":cap["capsule"],"key":inventory["manifest"][0]["key"]})).unwrap();
    assert_eq!(record["sha"], inventory["manifest"][0]["sha"]);
    assert!(record.as_object().unwrap().len() > 2);
}
#[test]
fn project_history_uses_completion_order_and_cursor() {
    let mut l = Lab::new();
    let second = send(
        &mut l.store,
        json!({"action":"project.create","name":"Later-created","charter":"Complete first"}),
    )
    .unwrap();
    send(
        &mut l.store,
        json!({"action":"project.state","project":second["project"],"state":"completed"}),
    )
    .unwrap();
    send(
        &mut l.store,
        json!({"action":"project.state","project":l.project,"state":"completed"}),
    )
    .unwrap();
    let first = send(
        &mut l.store,
        json!({"action":"history","kind":"projects","limit":1}),
    )
    .unwrap();
    assert_eq!(first[0]["id"], l.project);
    let next = send(
        &mut l.store,
        json!({"action":"history","kind":"projects","before":first[0]["cursor"]}),
    )
    .unwrap();
    assert_eq!(next[0]["id"], second["project"]);
}
#[test]
fn expired_messages_become_one_visible_dead_letter_even_after_restart() {
    let mut l = Lab::new();
    let t = l.task("Deadline");
    let m=send(&mut l.store,json!({"action":"message.send","recipient":l.worker["agent"],"task":t,"thread":"deadline","body":"Needs acknowledgement"})).unwrap();
    l.store
        .db
        .execute(
            "UPDATE messages SET deadline=? WHERE id=?",
            rusqlite::params![now() - 1, m["message"].as_str()],
        )
        .unwrap();
    l.store.tick().unwrap();
    l.store.tick().unwrap();
    let snapshot = send(&mut l.store, json!({"action":"snapshot"})).unwrap();
    assert_eq!(snapshot["incidents"].as_array().unwrap().len(), 1);
    drop(l.store);
    let mut store = Store::open(l.dir.path()).unwrap();
    store.tick().unwrap();
    assert_eq!(
        send(&mut store, json!({"action":"snapshot"})).unwrap()["incidents"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}
#[test]
fn canonical_observation_needs_fresh_working_state_and_same_runtime_generation() {
    let mut l = Lab::new();
    let t = l.task("Observe");
    l.claim(&t);
    l.store
        .db
        .execute("UPDATE attempts SET runtime_id='exact-runtime'", [])
        .unwrap();
    let stamp = chrono::DateTime::from_timestamp(now(), 0)
        .unwrap()
        .to_rfc3339();
    let mut observed = json!({"schema":"fm-fleet-snapshot.v1","tasks":[{"id":"exact-runtime","spawn_gen":"one","current_state":{"state":"unknown","freshness":"fresh","observed_at":stamp},"endpoint":{"exists":true},"paths":{"worktree":"/tmp/test-only-observation"}}]});
    assert_eq!(
        olympus_core::bridge::apply_observation(&l.store.db, &observed).unwrap(),
        0
    );
    observed["tasks"][0]["current_state"]["state"] = json!("working");
    assert_eq!(
        olympus_core::bridge::apply_observation(&l.store.db, &observed).unwrap(),
        1
    );
    observed["tasks"][0]["spawn_gen"] = json!("replacement");
    assert_eq!(
        olympus_core::bridge::apply_observation(&l.store.db, &observed).unwrap(),
        0
    );
    assert_eq!(
        send(&mut l.store, json!({"action":"snapshot"})).unwrap()["incidents"][0]["kind"],
        "runtime_generation"
    );
}
#[test]
fn backup_rejects_symlinked_parent_paths() {
    let l = Lab::new();
    let area = tempfile::tempdir().unwrap();
    let copy = area.path().join("backup");
    backup::create(&l.store, &copy).unwrap();
    let outside = area.path().join("outside");
    fs::create_dir(&outside).unwrap();
    fs::write(outside.join("record"), b"hidden").unwrap();
    std::os::unix::fs::symlink(&outside, copy.join("linked")).unwrap();
    let manifest = copy.join("manifest.json");
    let mut m: Value = serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
    m["files"]["linked/record"] = json!(olympus_core::hash(b"hidden"));
    fs::write(manifest, m.to_string()).unwrap();
    assert!(backup::verify(&copy).is_err());
}
#[test]
fn backup_includes_uncommitted_and_untracked_workspace_files() {
    let mut l = Lab::new();
    let area = tempfile::tempdir().unwrap();
    let repo = area.path().join("repo");
    fs::create_dir(&repo).unwrap();
    let git = |args: &[&str]| {
        let o = std::process::Command::new("git")
            .arg("-C")
            .arg(&repo)
            .args(args)
            .output()
            .unwrap();
        assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    };
    git(&["init"]);
    fs::write(repo.join("tracked"), "original").unwrap();
    git(&["add", "tracked"]);
    git(&[
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Fixture",
    ]);
    fs::write(repo.join("tracked"), "uncommitted").unwrap();
    fs::write(repo.join("untracked"), "new content").unwrap();
    let t = l.task("Workspace");
    l.claim(&t);
    l.store
        .db
        .execute("UPDATE attempts SET workspace=?", [repo.to_str()])
        .unwrap();
    let copy = area.path().join("snapshot");
    assert!(backup::create(&l.store, &copy).is_err());
    send(
        &mut l.store,
        json!({"action":"task.pause","task":t,"reason":"Runtime stopped and safe"}),
    )
    .unwrap();
    let good = area.path().join("good");
    let m = backup::create(&l.store, &good).unwrap();
    let prefix = m["workspaces"][0]["backup_prefix"].as_str().unwrap();
    assert_eq!(
        fs::read_to_string(good.join(prefix).join("files/tracked")).unwrap(),
        "uncommitted"
    );
    assert_eq!(
        fs::read_to_string(good.join(prefix).join("files/untracked")).unwrap(),
        "new content"
    );
    assert!(good.join(prefix).join("repository.bundle").is_file());
    backup::verify(&good).unwrap();
}
#[test]
fn teams_create_scoped_hierarchy_without_launching_unneeded_runtimes() {
    let mut l = Lab::new();
    let chief = send(
        &mut l.store,
        json!({"action":"agent.create","role":"chief"}),
    )
    .unwrap();
    let team=as_agent(&mut l.store,&chief,json!({"action":"team.create","project":l.project,"parent":chief["agent"],"roles":["senior","rust","quality"]})).unwrap();
    assert_eq!(team["specialists"].as_array().unwrap().len(), 3);
    assert_eq!(team["runtimes_launched"], 0);
    let capt = team["captain"]["agent"].as_str().unwrap();
    let list = send(&mut l.store, json!({"action":"snapshot"})).unwrap();
    assert_eq!(
        list["agents"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|a| a["parent"] == capt)
            .count(),
        3
    );
}
#[test]
fn dispatch_cannot_recommend_unverified_profiles_or_unevidenced_native_children() {
    let mut l = Lab::new();
    let t = l.task("Route");
    send(&mut l.store,json!({"action":"profile.register","profile":"fixture","harness":"codex","model":"fixture","version":"fixture-only","capabilities":{"roles":["rust"],"native_children":true}})).unwrap();
    assert!(
        send(&mut l.store, json!({"action":"dispatch.plan","task":t})).unwrap()["recommended"]
            .is_null()
    );
    let evidence = json!({"harness":"codex","version":"fixture-only","command":"Test fixture, no real harness","observed_result":"Synthetic contract only","real_harness":true,"checks":{"launch":true,"message":true,"interrupt":true,"resume":true,"stop":true}});
    assert!(send(
        &mut l.store,
        json!({"action":"profile.verify","profile":"fixture","evidence":evidence})
    )
    .is_err());
}

#[test]
fn snapshot_bounds_prompt_size_without_discarding_full_task_source() {
    let mut l = Lab::new();
    let body = "large evidence ".repeat(4000);
    let t=send(&mut l.store,json!({"action":"task.create","project":l.project,"title":"Large task","requirements":body})).unwrap();
    let snapshot = send(&mut l.store, json!({"action":"snapshot"})).unwrap();
    assert!(snapshot.to_string().len() < 10000);
    assert_eq!(snapshot["tasks"][0]["projection_truncated"], true);
    let detail = send(&mut l.store, json!({"action":"task.get","task":t["task"]})).unwrap();
    assert_eq!(detail["task"]["requirements"], body.trim());
}

#[test]
fn parent_fanout_headroom_cannot_be_stolen_by_another_parent() {
    let mut l = Lab::new();
    send(
        &mut l.store,
        json!({"action":"system.configure","global_cap":2,"project_cap":2}),
    )
    .unwrap();
    let parent = l.task("Parent needs one child");
    let other = l.task("Competing parent");
    let rival = send(
        &mut l.store,
        json!({"action":"agent.create","project":l.project,"role":"rust"}),
    )
    .unwrap();
    send(
        &mut l.store,
        json!({"action":"task.claim","task":parent,"agent":l.worker["agent"],"reserve_children":1}),
    )
    .unwrap();
    assert!(send(
        &mut l.store,
        json!({"action":"task.claim","task":other,"agent":rival["agent"]})
    )
    .is_err());
    as_agent(&mut l.store,&l.worker,json!({"action":"child.reserve","task":parent,"generation":1,"role":"researcher","title":"Child","requirements":"Bounded evidence"})).unwrap();
    assert_eq!(
        send(&mut l.store, json!({"action":"snapshot"})).unwrap()["counts"]["working"],
        2
    );
}

#[test]
fn inbox_previews_are_bounded_and_exact_messages_require_conversation_membership() {
    let mut l = Lab::new();
    let body = "full source ".repeat(3000);
    let message=as_agent(&mut l.store,&l.worker,json!({"action":"message.send","recipient":l.reviewer["agent"],"thread":"Long evidence","body":body})).unwrap();
    let inbox = as_agent(&mut l.store, &l.reviewer, json!({"action":"message.inbox"})).unwrap();
    assert!(inbox.to_string().len() < 2000);
    assert_eq!(inbox[0]["projection_truncated"], true);
    let exact = as_agent(
        &mut l.store,
        &l.reviewer,
        json!({"action":"message.get","message":message["message"]}),
    )
    .unwrap();
    assert_eq!(exact["body"], body.trim());
    let unrelated = send(
        &mut l.store,
        json!({"action":"agent.create","project":l.project,"role":"engineer"}),
    )
    .unwrap();
    assert!(as_agent(
        &mut l.store,
        &unrelated,
        json!({"action":"message.get","message":message["message"]})
    )
    .is_err());
}

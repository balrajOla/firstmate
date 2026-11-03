//! Calendar occurrences are durable obligations, never duplicate external effects.
use crate::*;
use chrono::{Datelike, Days, NaiveTime, TimeZone, Utc};
use chrono_tz::Tz;

pub fn timezone(db: &Connection, r: &Value) -> Result<Tz> {
    let name = r["timezone"]
        .as_str()
        .map(str::to_string)
        .unwrap_or(
            db.query_row("SELECT value FROM settings WHERE key='timezone'", [], |r| {
                r.get(0)
            })?,
        );
    name.parse()
        .map_err(|_| anyhow::anyhow!("unknown IANA timezone: {name}"))
}

/// A repeated wall time fires once, at its first occurrence. A skipped time moves
/// forward to the first valid local minute. Calendar days are not 24h durations.
pub fn next(cadence: &str, zone: Tz, time: &str, weekday: i64, after: i64) -> Result<i64> {
    if !matches!(cadence, "daily" | "weekdays" | "weekly") {
        bail!("cadence must be daily, weekdays or weekly")
    }
    if !(0..=6).contains(&weekday) {
        bail!("weekday must be Monday=0 through Sunday=6")
    }
    let clock = NaiveTime::parse_from_str(time, "%H:%M").context("time must be HH:MM")?;
    let date = Utc
        .timestamp_opt(after, 0)
        .single()
        .context("timestamp out of range")?
        .with_timezone(&zone)
        .date_naive();
    for offset in 0..9 {
        let day = date
            .checked_add_days(Days::new(offset))
            .context("date overflow")?;
        let dow = day.weekday().num_days_from_monday() as i64;
        if cadence == "weekdays" && dow >= 5 || cadence == "weekly" && dow != weekday {
            continue;
        }
        for minute in 0..181 {
            let local = day.and_time(clock) + chrono::Duration::minutes(minute);
            if let Some(at) = zone.from_local_datetime(&local).earliest() {
                if at.timestamp() > after {
                    return Ok(at.timestamp());
                }
                break;
            }
        }
    }
    bail!("no calendar occurrence within nine days")
}

pub(crate) fn mutate(db: &Connection, actor: &Principal, r: &Value) -> Result<Value> {
    require_manager(actor)?;
    match required(r, "action")?.as_str() {
        "schedule.create" => {
            let project = required(r, "project")?;
            actor.scope(&project)?;
            row(db, "SELECT id FROM projects WHERE id=?", [&project])?;
            let cadence = required(r, "cadence")?;
            let zone = timezone(db, r)?;
            let time = r["time"].as_str().unwrap_or("09:00");
            let weekday = number(r, "weekday", 0);
            let next_run = next(&cadence, zone, time, weekday, now() - 1)?;
            let sid = id("S");
            db.execute("INSERT INTO schedules(id,project,title,requirements,role,cadence,timezone,local_time,weekday,next_run,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",params![sid,project,required(r,"title")?,required(r,"requirements")?,r["role"].as_str().unwrap_or("engineer"),cadence,zone.to_string(),time,weekday,next_run,now()])?;
            Ok(
                json!({"schedule":sid,"next_run":next_run,"timezone":zone.to_string(),"misfire":"coalesce","overlap":"wait"}),
            )
        }
        "schedule.state" => {
            let sid = required(r, "schedule")?;
            let s = row(db, "SELECT * FROM schedules WHERE id=?", [&sid])?;
            actor.scope(s["project"].as_str().unwrap())?;
            let state = required(r, "state")?;
            if !matches!(state.as_str(), "active" | "paused" | "cancelled") {
                bail!("invalid schedule state")
            }
            db.execute(
                "UPDATE schedules SET state=? WHERE id=?",
                params![state, sid],
            )?;
            Ok(json!({"schedule":sid,"state":state}))
        }
        "task.due" => {
            let tid = required(r, "task")?;
            task(db, actor, &tid)?;
            let due = r["due"]
                .as_i64()
                .filter(|x| *x > 0)
                .context("positive Unix timestamp due required")?;
            db.execute("INSERT INTO task_dates VALUES(?,?) ON CONFLICT(task) DO UPDATE SET due=excluded.due",params![tid,due])?;
            Ok(json!({"task":tid,"due":due}))
        }
        _ => bail!("unknown calendar action"),
    }
}

pub(crate) fn tick(db: &Connection, at: i64) -> Result<usize> {
    let due=rows(db,"SELECT s.* FROM schedules s JOIN projects p ON p.id=s.project WHERE s.state='active' AND p.state='active' AND s.next_run<=? ORDER BY next_run,id LIMIT 100",[at])?;
    let mut created = 0;
    for s in due {
        let sid = s["id"].as_str().unwrap();
        let overlap:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM schedule_runs r JOIN tasks t ON r.task=t.id WHERE r.schedule=? AND t.state NOT IN ('accepted','cancelled'))",[sid],|r|r.get(0))?;
        if overlap {
            continue;
        }
        let result = crate::actions::mutate(
            db,
            &Principal::operator(),
            &json!({"action":"task.create","project":s["project"],"title":s["title"],"requirements":s["requirements"],"role":s["role"]}),
            Path::new(""),
        )?;
        let tid = result["task"].as_str().unwrap();
        db.execute(
            "INSERT INTO schedule_runs VALUES(?,?,?)",
            params![sid, s["next_run"].as_i64(), tid],
        )?;
        db.execute(
            "INSERT INTO task_dates VALUES(?,?)",
            params![tid, s["next_run"].as_i64()],
        )?;
        let zone: Tz = s["timezone"].as_str().unwrap().parse()?;
        let following = next(
            s["cadence"].as_str().unwrap(),
            zone,
            s["local_time"].as_str().unwrap(),
            s["weekday"].as_i64().unwrap(),
            at,
        )?;
        db.execute(
            "UPDATE schedules SET next_run=? WHERE id=?",
            params![following, sid],
        )?;
        event(
            db,
            &Principal::operator(),
            "schedule.fired",
            tid,
            &json!({"schedule":sid,"occurrence":s["next_run"],"next_run":following}),
        )?;
        created += 1;
    }
    Ok(created)
}

pub(crate) fn read(db: &Connection, actor: &Principal, r: &Value) -> Result<Value> {
    let scope = actor.project.as_deref().unwrap_or("");
    let project = optional(r, "project");
    if !project.is_empty() {
        actor.scope(&project)?;
    }
    if r["action"] == "schedules.list" {
        return Ok(json!(rows(db,"SELECT s.*,p.name AS project_name,(SELECT t.state FROM schedule_runs z JOIN tasks t ON t.id=z.task WHERE z.schedule=s.id ORDER BY z.occurrence DESC LIMIT 1) AS last_state FROM schedules s JOIN projects p ON p.id=s.project WHERE (? OR s.project=?) AND (?='' OR s.project=?) ORDER BY next_run,id LIMIT 200",params![actor.global(),scope,project,project])?));
    }
    let zone = timezone(db, r)?;
    let date = Utc
        .timestamp_opt(now(), 0)
        .unwrap()
        .with_timezone(&zone)
        .date_naive();
    let start_date = if r["view"] == "week" {
        date.checked_sub_days(Days::new(date.weekday().num_days_from_monday() as u64))
            .context("date overflow")?
    } else {
        date
    };
    let start = zone
        .from_local_datetime(&start_date.and_hms_opt(0, 0, 0).unwrap())
        .earliest()
        .context("local midnight does not exist")?
        .timestamp();
    let days = if r["view"] == "week" {
        7 - date.weekday().num_days_from_monday() as u64
    } else {
        1
    };
    let end = zone
        .from_local_datetime(
            &date
                .checked_add_days(Days::new(days))
                .unwrap()
                .and_hms_opt(0, 0, 0)
                .unwrap(),
        )
        .earliest()
        .context("local midnight does not exist")?
        .timestamp();
    let jobs=rows(db,"SELECT t.*,p.name AS project_name,d.due,s.schedule,CASE WHEN t.state='accepted' THEN 'completed' WHEN d.due<? THEN 'overdue' WHEN d.due<? THEN 'due' ELSE 'in_progress' END AS bucket FROM tasks t JOIN projects p ON p.id=t.project LEFT JOIN task_dates d ON d.task=t.id LEFT JOIN schedule_runs s ON s.task=t.id WHERE (? OR t.project=?) AND (?='' OR t.project=?) AND ((t.state NOT IN ('accepted','cancelled') AND (d.due<? OR t.state IN ('running','recovering','blocked','unknown','review'))) OR (t.state='accepted' AND t.completed>=?)) ORDER BY CASE WHEN t.state='accepted' THEN 1 ELSE 0 END,d.due IS NULL,d.due,t.priority DESC LIMIT 500",params![now(),end,actor.global(),scope,project,project,end,start])?;
    Ok(
        json!({"timezone":zone.to_string(),"start":start,"end":end,"jobs":jobs,"view":r["view"],"schedules":read(db,actor,&json!({"action":"schedules.list","project":project}))?}),
    )
}

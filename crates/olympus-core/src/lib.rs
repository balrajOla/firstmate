//! Durable portfolio control. Runtime process truth remains owned by Firstmate.
use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

mod actions;
pub mod automation;
pub mod backup;
pub mod bridge;
pub mod demo;
mod learning;
mod portfolio;
pub mod protocol;
pub mod scheduling;

pub const SCHEMA: &str = "olympus.v1";
pub const MAX_REQUEST: usize = 1_048_576;
pub fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}
pub fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn id(prefix: &str) -> String {
    format!(
        "{prefix}-{}",
        &uuid::Uuid::new_v4().simple().to_string()[..16]
    )
}
pub fn private_dir(path: &Path) -> Result<()> {
    fs::create_dir_all(path)?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}
pub fn private_write(path: &Path, body: &[u8]) -> Result<()> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let temp = path.with_extension(format!("{}.tmp", id("write")));
    let mut f = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(&temp)?;
    f.write_all(body)?;
    f.sync_all()?;
    fs::rename(&temp, path)?;
    if let Some(parent) = path.parent() {
        fs::File::open(parent)?.sync_all()?;
    }
    Ok(())
}
pub fn home_default() -> PathBuf {
    std::env::var_os("OLYMPUS_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/share/olympus")
        })
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Principal {
    pub id: String,
    pub project: Option<String>,
    pub role: String,
}
impl Principal {
    pub fn operator() -> Self {
        Self {
            id: "operator".into(),
            project: None,
            role: "operator".into(),
        }
    }
    pub fn is_operator(&self) -> bool {
        self.role == "operator"
    }
    pub fn manages(&self) -> bool {
        matches!(
            self.role.as_str(),
            "operator" | "chief" | "captain" | "manager"
        )
    }
    pub fn global(&self) -> bool {
        self.is_operator() || self.role == "chief"
    }
    pub fn scope(&self, project: &str) -> Result<()> {
        if self.global() || self.project.as_deref() == Some(project) {
            Ok(())
        } else {
            bail!("scope denied: project {project}")
        }
    }
}

pub struct Store {
    pub db: Connection,
    pub home: PathBuf,
}
impl Store {
    pub fn open(home: &Path) -> Result<Self> {
        private_dir(home)?;
        private_dir(&home.join("vault"))?;
        private_dir(&home.join("objects"))?;
        let db = Connection::open(home.join("olympus.db"))?;
        db.busy_timeout(Duration::from_secs(5))?;
        db.execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;",
        )?;
        let existing: Option<String> = db
            .query_row("SELECT value FROM settings WHERE key='schema'", [], |r| {
                r.get(0)
            })
            .optional()
            .unwrap_or(None);
        if existing
            .as_deref()
            .is_some_and(|v| !matches!(v, "1" | "2" | "3"))
        {
            bail!("incompatible database schema; use the matching release or restore a backup")
        }
        db.execute_batch(include_str!("schema.sql"))?;
        if existing.as_deref() != Some("3") {
            db.execute_batch(include_str!("schema-v2.sql"))?;
            db.execute_batch(include_str!("schema-v3.sql"))?;
        }
        fs::set_permissions(home.join("olympus.db"), fs::Permissions::from_mode(0o600))?;
        if !home.join("operator.token").exists() {
            // create_new prevents two initialisers replacing one another's token.
            use std::{io::Write, os::unix::fs::OpenOptionsExt};
            match fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .mode(0o600)
                .open(home.join("operator.token"))
            {
                Ok(mut f) => {
                    f.write_all(uuid::Uuid::new_v4().simple().to_string().as_bytes())?;
                    f.sync_all()?;
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(e) => return Err(e.into()),
            }
        }
        Ok(Self {
            db,
            home: home.to_path_buf(),
        })
    }
    pub fn authenticate(&self, token: &str) -> Result<Principal> {
        let op = fs::read_to_string(self.home.join("operator.token"))?;
        if hash(token.trim().as_bytes()) == hash(op.trim().as_bytes()) {
            return Ok(Principal::operator());
        }
        self.db
            .query_row(
                "SELECT id,project,role FROM agents WHERE token_hash=? AND state!='retired'",
                [hash(token.as_bytes())],
                |r| {
                    Ok(Principal {
                        id: r.get(0)?,
                        project: r.get(1)?,
                        role: r.get(2)?,
                    })
                },
            )
            .optional()?
            .context("invalid or retired agent credential")
    }
    pub fn execute(&mut self, actor: &Principal, req: &Value) -> Result<Value> {
        if serde_json::to_vec(req)?.len() > MAX_REQUEST {
            bail!("request exceeds 1 MiB")
        }
        let action = required(req, "action")?;
        if actions::is_read(&action) {
            return actions::read(&self.db, actor, req);
        }
        let key = required(req, "request_id")?;
        if key.len() > 128 {
            bail!("request_id exceeds 128 characters")
        }
        let request_hash = hash(&serde_json::to_vec(req)?);
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some((old_hash, result)) = tx
            .query_row(
                "SELECT request_hash,result FROM receipts WHERE actor=? AND id=?",
                params![actor.id, key],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
            )
            .optional()?
        {
            if old_hash != request_hash {
                bail!("request_id was already used with different content")
            }
            return Ok(serde_json::from_str(&result)?);
        }
        let result = actions::mutate(&tx, actor, req, &self.home)?;
        let mut receipt = result.clone();
        if let Some(o) = receipt.as_object_mut() {
            if o.remove("token").is_some() {
                o.insert("credential_delivered_once".into(), json!(true));
            }
        }
        tx.execute(
            "INSERT INTO receipts VALUES(?,?,?,?)",
            params![actor.id, key, request_hash, receipt.to_string()],
        )?;
        tx.commit()?;
        Ok(result)
    }
    pub fn tick(&mut self) -> Result<Value> {
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let result = actions::mutate(
            &tx,
            &Principal::operator(),
            &json!({"action":"system.tick"}),
            &self.home,
        )?;
        tx.commit()?;
        Ok(result)
    }
}

pub(crate) fn required(v: &Value, key: &str) -> Result<String> {
    let s = v
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .with_context(|| format!("missing nonempty string: {key}"))?;
    if s.len() > 65_536 {
        bail!("{key} exceeds 64 KiB")
    }
    Ok(s.to_string())
}
pub(crate) fn optional(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or("").to_string()
}
pub(crate) fn number(v: &Value, key: &str, default: i64) -> i64 {
    v.get(key).and_then(Value::as_i64).unwrap_or(default)
}
pub(crate) fn row(db: &Connection, sql: &str, args: impl rusqlite::Params) -> Result<Value> {
    rows(db, sql, args)?
        .into_iter()
        .next()
        .context("record not found")
}
pub(crate) fn rows(db: &Connection, sql: &str, args: impl rusqlite::Params) -> Result<Vec<Value>> {
    let mut stmt = db.prepare(sql)?;
    let names = stmt
        .column_names()
        .into_iter()
        .map(str::to_string)
        .collect::<Vec<_>>();
    let iter = stmt.query_map(args, |r| {
        let mut obj = serde_json::Map::new();
        for (i, n) in names.iter().enumerate() {
            use rusqlite::types::ValueRef;
            let v = match r.get_ref(i)? {
                ValueRef::Null => Value::Null,
                ValueRef::Integer(n) => json!(n),
                ValueRef::Real(n) => json!(n),
                ValueRef::Text(s) => json!(String::from_utf8_lossy(s)),
                ValueRef::Blob(_) => Value::Null,
            };
            obj.insert(n.clone(), v);
        }
        Ok(Value::Object(obj))
    })?;
    Ok(iter.collect::<rusqlite::Result<Vec<_>>>()?)
}
pub(crate) fn event(
    db: &Connection,
    actor: &Principal,
    kind: &str,
    entity: &str,
    body: &Value,
) -> Result<()> {
    db.execute(
        "INSERT INTO events(kind,entity,actor,body,created) VALUES(?,?,?,?,?)",
        params![kind, entity, actor.id, body.to_string(), now()],
    )?;
    Ok(())
}
pub(crate) fn task(db: &Connection, actor: &Principal, id: &str) -> Result<Value> {
    let t = row(db, "SELECT * FROM tasks WHERE id=?", [id])?;
    actor.scope(t["project"].as_str().context("task project")?)?;
    Ok(t)
}
pub(crate) fn incident(
    db: &Connection,
    task: Option<&str>,
    kind: &str,
    reason: &str,
    owner: &str,
) -> Result<()> {
    db.execute("INSERT OR IGNORE INTO incidents(id,task,kind,reason,owner,next_check,created) VALUES(?,?,?,?,?,?,?)",params![id("I"),task,kind,reason,owner,now()+60,now()])?;
    Ok(())
}
pub(crate) fn response(db: &Connection, project: &str, task: &str, body: &str) -> Result<String> {
    let key = id("R");
    db.execute(
        "INSERT INTO responses(id,project,task,body,created) VALUES(?,?,?,?,?)",
        params![key, project, task, body, now()],
    )?;
    Ok(key)
}
pub(crate) fn require_manager(actor: &Principal) -> Result<()> {
    if !actor.manages() {
        bail!("manager authority required")
    }
    Ok(())
}
pub(crate) fn require_operator(actor: &Principal) -> Result<()> {
    if !actor.is_operator() {
        bail!("operator authority required")
    }
    Ok(())
}
pub(crate) fn current_attempt(
    db: &Connection,
    actor: &Principal,
    req: &Value,
) -> Result<(Value, Value)> {
    let t = task(db, actor, &required(req, "task")?)?;
    let a = row(
        db,
        "SELECT * FROM attempts WHERE task=? AND generation=?",
        params![t["id"].as_str(), number(req, "generation", -1)],
    )?;
    if t["generation"] != a["generation"]
        || !matches!(
            a["state"].as_str(),
            Some("running" | "review" | "paused" | "unknown" | "recovering")
        )
    {
        bail!("stale or fenced attempt")
    }
    if !actor.is_operator() && a["agent"] != actor.id {
        bail!("only current attempt owner may report work")
    }
    if t["version"] != a["version"] {
        bail!("requirements changed; acknowledge the new version before reporting")
    }
    Ok((t, a))
}

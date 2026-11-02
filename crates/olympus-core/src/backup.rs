//! Consistent database snapshots and fail-closed restore into a new home.
use crate::*;
use std::collections::BTreeMap;

pub fn create(store: &Store, destination: &Path) -> Result<Value> {
    if destination.exists() {
        bail!("backup destination already exists")
    }
    private_dir(destination)?;
    let result = (|| {
        store
            .db
            .backup("main", destination.join("olympus.db"), None)?;
        let db = Connection::open(destination.join("olympus.db"))?;
        let mut files = BTreeMap::<String, String>::new();
        let mut workspaces = vec![];
        private_dir(&destination.join("vault"))?;
        for record in rows(&db, "SELECT * FROM knowledge ORDER BY id", [])? {
            let name = format!("vault/{}.md", record["id"].as_str().unwrap());
            let text = format!(
                "# {}\n\n{}\n\nSources: {}\nStatus: {}\nSupersedes: {}\n",
                record["title"].as_str().unwrap(),
                record["body"].as_str().unwrap(),
                record["sources"].as_str().unwrap(),
                record["status"].as_str().unwrap(),
                record["supersedes"]
            );
            private_write(&destination.join(&name), text.as_bytes())?;
            files.insert(name, hash(text.as_bytes()));
        }
        // A backup must not quietly omit an active worktree. Quiescence is required.
        let spaces = rows(
            &db,
            "SELECT DISTINCT workspace FROM attempts WHERE workspace!=''",
            [],
        )?;
        for space in spaces {
            let original = PathBuf::from(space["workspace"].as_str().unwrap());
            let active:i64=db.query_row("SELECT COUNT(*) FROM attempts WHERE workspace=? AND state IN ('running','unknown','recovering','review')",[original.to_string_lossy().as_ref()],|r|r.get(0))?;
            if active > 0 {
                bail!("workspace {} is active or uncertain; stop its runtime and checkpoint before backing up",original.display())
            }
            let prefix = format!(
                "workspaces/{}",
                &hash(original.to_string_lossy().as_bytes())[..16]
            );
            let listing = git(
                &original,
                &[
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                ],
            )?;
            let names = listing
                .split(|c| *c == 0)
                .filter(|n| !n.is_empty())
                .map(|n| String::from_utf8(n.to_vec()))
                .collect::<std::result::Result<Vec<_>, _>>()?;
            if names.len() > 20000 {
                bail!("workspace exceeds 20,000-file backup bound; configure an external snapshot")
            }
            let head = String::from_utf8(git(&original, &["rev-parse", "HEAD"])?)?
                .trim()
                .to_string();
            let mut copied = vec![];
            let mut bytes = 0u64;
            for name in &names {
                safe_relative(name)?;
                let source = contained(&original, name)?;
                if !source.exists() {
                    continue;
                } // A tracked deletion is represented by the diff below.
                if source.symlink_metadata()?.file_type().is_symlink() {
                    bail!(
                        "symlink requires an external workspace snapshot: {}",
                        source.display()
                    )
                }
                if !source.is_file() {
                    bail!(
                        "non-file workspace entry requires an external snapshot: {}",
                        source.display()
                    )
                }
                bytes += source.metadata()?.len();
                if bytes > 2_147_483_648 {
                    bail!("workspace exceeds 2 GiB backup bound")
                }
                let body = fs::read(&source)?;
                let digest = hash(&body);
                let relative = format!("{prefix}/files/{name}");
                private_dir(destination.join(&relative).parent().unwrap())?;
                private_write(&destination.join(&relative), &body)?;
                files.insert(relative, digest.clone());
                copied.push((source, digest));
            }
            let diff = git(&original, &["diff", "--binary", "HEAD"])?;
            let diff_name = format!("{prefix}/uncommitted.patch");
            private_dir(destination.join(&diff_name).parent().unwrap())?;
            private_write(&destination.join(&diff_name), &diff)?;
            files.insert(diff_name, hash(&diff));
            let bundle_name = format!("{prefix}/repository.bundle");
            git(
                &original,
                &[
                    "bundle",
                    "create",
                    destination
                        .join(&bundle_name)
                        .to_str()
                        .context("UTF-8 backup path required")?,
                    "--all",
                ],
            )?;
            files.insert(
                bundle_name.clone(),
                hash(&fs::read(destination.join(&bundle_name))?),
            );
            if listing
                != git(
                    &original,
                    &[
                        "ls-files",
                        "-z",
                        "--cached",
                        "--others",
                        "--exclude-standard",
                    ],
                )?
                || head != String::from_utf8(git(&original, &["rev-parse", "HEAD"])?)?.trim()
                || diff != git(&original, &["diff", "--binary", "HEAD"])?
            {
                bail!("workspace changed during backup; snapshot refused")
            }
            for (path, digest) in copied {
                if hash(&fs::read(path)?) != digest {
                    bail!("workspace file changed during backup; snapshot refused")
                }
            }
            workspaces.push(
                json!({"original":original,"backup_prefix":prefix,"head":head,"files":names.len()}),
            );
        }
        let check: String = db.query_row("PRAGMA integrity_check", [], |r| r.get(0))?;
        if check != "ok" {
            bail!("snapshot integrity failed: {check}")
        }
        let cursor: i64 =
            db.query_row("SELECT COALESCE(MAX(seq),0) FROM events", [], |r| r.get(0))?;
        drop(db);
        files.insert(
            "olympus.db".into(),
            hash(&fs::read(destination.join("olympus.db"))?),
        );
        let manifest = json!({"schema":"olympus.backup.v1","created":now(),"event_cursor":cursor,"files":files,"workspaces":workspaces,"excluded":["operator credential: regenerate on restore","external provider credentials: reconnect separately","gitignored files: use an explicit external backup if needed"],"dispatch_on_restore":false});
        private_write(
            &destination.join("manifest.json"),
            serde_json::to_string_pretty(&manifest)?.as_bytes(),
        )?;
        verify(destination)?;
        Ok(manifest)
    })();
    // Keep failed snapshots as evidence, clearly marked; never advertise them as valid.
    if let Err(e) = &result {
        let _ = private_write(&destination.join("FAILED.txt"), e.to_string().as_bytes());
    }
    result
}

pub fn verify(source: &Path) -> Result<Value> {
    if source.join("FAILED.txt").exists() {
        bail!("backup is marked failed")
    }
    let m: Value = serde_json::from_slice(&fs::read(source.join("manifest.json"))?)?;
    if m["schema"] != "olympus.backup.v1" {
        bail!("unsupported backup schema")
    }
    let files = m["files"].as_object().context("missing file manifest")?;
    if !files.contains_key("olympus.db") {
        bail!("database is absent from the manifest")
    }
    for (name, digest) in files {
        safe_relative(name)?;
        let p = contained(source, name)?;
        if p.symlink_metadata()?.file_type().is_symlink() {
            bail!("backup contains a symlink")
        };
        if hash(&fs::read(p)?) != digest.as_str().context("invalid digest")? {
            bail!("checksum mismatch: {name}")
        }
    }
    let db = Connection::open_with_flags(
        source.join("olympus.db"),
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
    )?;
    if db.query_row("PRAGMA integrity_check", [], |r| r.get::<_, String>(0))? != "ok" {
        bail!("database integrity failed")
    }
    if !rows(&db, "PRAGMA foreign_key_check", [])?.is_empty() {
        bail!("database has broken references")
    }
    Ok(m)
}

pub fn restore(source: &Path, target: &Path) -> Result<Value> {
    let m = verify(source)?;
    if target.exists() {
        bail!("restore requires a new empty destination; existing homes are never overwritten")
    }
    private_dir(target)?;
    for name in m["files"].as_object().unwrap().keys() {
        let dest = target.join(name);
        private_dir(dest.parent().unwrap())?;
        private_write(&dest, &fs::read(source.join(name))?)?;
    }
    let store = Store::open(target)?;
    store.db.execute(
        "UPDATE settings SET value='false' WHERE key='dispatch_enabled'",
        [],
    )?;
    store.db.execute("UPDATE attempts SET state='unknown' WHERE state IN ('running','review','paused','recovering')",[])?;
    store.db.execute("UPDATE tasks SET state='unknown',reason='Restored snapshot; reconcile original runtimes before resuming' WHERE state IN ('running','review','paused','recovering')",[])?;
    store.db.execute("UPDATE profiles SET verified=0", [])?;
    store.db.execute(
        "UPDATE operations SET state='uncertain' WHERE state='pending'",
        [],
    )?;
    // Revoke restored agent capabilities; original runtimes may still be alive.
    store.db.execute(
        "UPDATE agents SET token_hash=lower(hex(randomblob(32)))",
        [],
    )?;
    store.db.execute("DELETE FROM receipts", [])?;
    private_write(
        &target.join("restore-manifest.json"),
        serde_json::to_string_pretty(&m)?.as_bytes(),
    )?;
    Ok(
        json!({"restored":true,"home":target,"dispatch_enabled":false,"profiles_require_reverification":true,"workspace_snapshots":"workspaces/; inspect and reconstruct in a new location before remapping","agent_credentials_revoked":true}),
    )
}

fn contained(root: &Path, name: &str) -> Result<PathBuf> {
    safe_relative(name)?;
    let mut current = root.to_path_buf();
    for component in Path::new(name).components() {
        current.push(component.as_os_str());
        if let Ok(meta) = current.symlink_metadata() {
            if meta.file_type().is_symlink() {
                bail!("symlinked snapshot path is forbidden")
            }
        }
    }
    Ok(current)
}
fn safe_relative(name: &str) -> Result<()> {
    if Path::new(name).is_absolute()
        || Path::new(name)
            .components()
            .any(|p| !matches!(p, std::path::Component::Normal(_)))
    {
        bail!("unsafe backup path")
    };
    Ok(())
}
fn git(dir: &Path, args: &[&str]) -> Result<Vec<u8>> {
    let o = std::process::Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .env("GIT_TERMINAL_PROMPT", "0")
        .output()?;
    if !o.status.success() {
        bail!(
            "git backup step failed: {}",
            String::from_utf8_lossy(&o.stderr)
        )
    };
    Ok(o.stdout)
}

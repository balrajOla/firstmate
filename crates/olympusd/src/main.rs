use anyhow::{Context, Result};
use clap::Parser;
use fs2::FileExt;
use olympus_core::{bridge, demo, home_default, private_dir, private_write, protocol, Store};
use serde_json::Value;
use std::{
    fs,
    io::Write,
    os::unix::{
        fs::{FileTypeExt, OpenOptionsExt, PermissionsExt},
        net::UnixListener,
    },
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant},
};

#[derive(Parser)]
#[command(
    version,
    about = "Durable Olympus coordinator; independent of any model conversation"
)]
struct Args {
    #[arg(long)]
    home: Option<PathBuf>,
}
enum Listener {
    Unix(UnixListener),
    Tcp(std::net::TcpListener),
}
impl Listener {
    fn accept(&self) -> std::io::Result<protocol::Stream> {
        match self {
            Self::Unix(l) => l.accept().map(|(s, _)| protocol::Stream::Unix(s)),
            Self::Tcp(l) => l.accept().map(|(s, _)| protocol::Stream::Tcp(s)),
        }
    }
}
fn main() {
    if let Err(e) = run() {
        eprintln!("olympusd: {e:#}");
        std::process::exit(1)
    }
}
fn run() -> Result<()> {
    let home = Args::parse().home.unwrap_or_else(home_default);
    private_dir(&home)?;
    let mut lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .mode(0o600)
        .open(home.join("daemon.lock"))?;
    lock.try_lock_exclusive()
        .context("another coordinator owns this home")?;
    lock.set_len(0)?;
    writeln!(lock, "{}", std::process::id())?;
    lock.sync_all()?;
    let mut store = Store::open(&home)?;
    let socket = home.join("olympus.sock");
    if socket.exists() {
        if !socket.symlink_metadata()?.file_type().is_socket() {
            anyhow::bail!("socket path is occupied by a non-socket file")
        };
        fs::remove_file(&socket)?;
    }
    let listener = match UnixListener::bind(&socket) {
        Ok(l) => {
            fs::set_permissions(&socket, fs::Permissions::from_mode(0o600))?;
            l.set_nonblocking(true)?;
            private_write(&home.join("endpoint.json"), b"{\"transport\":\"unix\"}")?;
            Listener::Unix(l)
        }
        Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
            let l = std::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))?;
            l.set_nonblocking(true)?;
            private_write(
                &home.join("endpoint.json"),
                serde_json::to_string(
                    &serde_json::json!({"transport":"tcp","address":l.local_addr()?.to_string()}),
                )?
                .as_bytes(),
            )?;
            Listener::Tcp(l)
        }
        Err(e) => return Err(e.into()),
    };
    let stopping = Arc::new(AtomicBool::new(false));
    let clients = Arc::new(AtomicUsize::new(0));
    let mut checked = Instant::now() - Duration::from_secs(2);
    let bridge_busy = Arc::new(AtomicBool::new(false));
    let observer_busy = Arc::new(AtomicBool::new(false));
    let mut observed = Instant::now() - Duration::from_secs(11);
    let simulator: String =
        store
            .db
            .query_row("SELECT value FROM settings WHERE key='mode'", [], |r| {
                r.get(0)
            })?;
    eprintln!("Olympus ready at {} (mode={simulator})", socket.display());
    while !stopping.load(Ordering::Relaxed) {
        match listener.accept() {
            Ok(stream) => {
                if clients.load(Ordering::Relaxed) >= 32 {
                    drop(stream);
                } else {
                    clients.fetch_add(1, Ordering::Relaxed);
                    let home = home.clone();
                    let clients = Arc::clone(&clients);
                    let stopping = Arc::clone(&stopping);
                    thread::spawn(move || {
                        match protocol::serve_connection(stream, &home) {
                            Ok(true) => stopping.store(true, Ordering::Relaxed),
                            Ok(false) => {}
                            Err(e) => eprintln!("client request failed: {e}"),
                        };
                        clients.fetch_sub(1, Ordering::Relaxed);
                    });
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(e) => return Err(e.into()),
        }
        if checked.elapsed() >= Duration::from_secs(1) {
            if simulator == "simulator" {
                demo::tick(&mut store)?;
            }
            if let Err(e) = store.tick() {
                eprintln!("supervision error: {e}")
            }
            // Slow runtime effects use one bounded worker, separate from the socket and supervisor.
            if home.join("firstmate.json").exists() && !bridge_busy.swap(true, Ordering::SeqCst) {
                let home = home.clone();
                let busy = Arc::clone(&bridge_busy);
                thread::spawn(move || {
                    let result = (|| -> Result<()> {
                        let cfg: Value =
                            serde_json::from_slice(&fs::read(home.join("firstmate.json"))?)?;
                        if cfg["execute"] != true {
                            return Ok(());
                        }
                        let mut worker = Store::open(&home)?;
                        let pending=worker.db.query_row("SELECT id FROM operations WHERE state='pending' ORDER BY created LIMIT 1",[],|r|r.get::<_,String>(0));
                        if let Ok(op) = pending {
                            let root = cfg["root"].as_str().context("Firstmate root missing")?;
                            let fm_home = cfg["home"].as_str().context("Firstmate home missing")?;
                            if let Err(e) = bridge::perform(
                                &mut worker,
                                std::path::Path::new(root),
                                std::path::Path::new(fm_home),
                                &op,
                            ) {
                                worker.db.execute("UPDATE operations SET state='refused',receipt=? WHERE id=? AND state='pending'",[e.to_string(),op])?;
                                return Err(e);
                            }
                        }
                        Ok(())
                    })();
                    if let Err(e) = result {
                        eprintln!("runtime bridge: {e}")
                    }
                    busy.store(false, Ordering::SeqCst);
                });
            }
            if home.join("firstmate.json").exists()
                && observed.elapsed() >= Duration::from_secs(10)
                && !observer_busy.swap(true, Ordering::SeqCst)
            {
                let home = home.clone();
                let busy = Arc::clone(&observer_busy);
                observed = Instant::now();
                thread::spawn(move || {
                    let result = (|| -> Result<()> {
                        let cfg: Value =
                            serde_json::from_slice(&fs::read(home.join("firstmate.json"))?)?;
                        let snapshot = bridge::observe(
                            std::path::Path::new(cfg["root"].as_str().context("root missing")?),
                            std::path::Path::new(cfg["home"].as_str().context("home missing")?),
                            &home.join("bridge-logs"),
                        )?;
                        let mut worker = Store::open(&home)?;
                        worker.execute(&olympus_core::Principal::operator(),&serde_json::json!({"action":"runtime.observe","snapshot":snapshot,"request_id":olympus_core::id("observation")}))?;
                        Ok(())
                    })();
                    if let Err(e) = result {
                        eprintln!("runtime observation: {e}")
                    };
                    busy.store(false, Ordering::SeqCst);
                });
            }
            checked = Instant::now();
        }
        thread::sleep(Duration::from_millis(20));
    }
    let deadline = Instant::now() + Duration::from_secs(4);
    while clients.load(Ordering::Relaxed) > 0 && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(20));
    }
    if socket.exists() {
        fs::remove_file(&socket)?;
    }
    fs::remove_file(home.join("endpoint.json"))?;
    FileExt::unlock(&lock)?;
    Ok(())
}

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use olympus_core::{
    backup, bridge, demo, home_default, id, private_dir, private_write, protocol, Store,
};
use serde_json::{json, Value};
use std::{
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

#[derive(Parser)]
#[command(
    version,
    about = "Olympus portfolio, agents, durable messages and terminal command view"
)]
struct Args {
    #[arg(long, global = true)]
    home: Option<PathBuf>,
    #[arg(long, global = true)]
    token_file: Option<PathBuf>,
    #[command(subcommand)]
    command: Cmd,
}
#[derive(Subcommand)]
enum Cmd {
    /// Create durable local state without starting models.
    Init,
    /// Start the coordinator independently of this terminal.
    Start,
    /// Stop only the coordinator; worker workspaces are preserved.
    Stop,
    /// Submit a JSON action. Use '-' to read from stdin.
    Request {
        #[arg(default_value = "-")]
        json: String,
    },
    /// Inspect durable portfolio state.
    Status,
    /// Route a request, recover related project context and queue a follow-up.
    Submit {
        text: String,
        #[arg(long, conflicts_with = "new_project")]
        project: Option<String>,
        #[arg(long, default_value = "engineer")]
        role: String,
        #[arg(long, value_parser=["routine","standard","complex"])]
        complexity: Option<String>,
        /// Force a distinct project with this name; the request becomes its charter.
        #[arg(long, conflicts_with = "task")]
        new_project: Option<String>,
        /// Amend this exact task instead of creating a follow-up.
        #[arg(long, requires = "expected_version")]
        task: Option<String>,
        #[arg(long, requires = "task")]
        expected_version: Option<i64>,
    },
    /// Today's or this week's work in the configured local timezone.
    Agenda {
        #[arg(default_value="today",value_parser=["today","week"])]
        view: String,
    },
    /// Enable/disable automatic dispatch and review for an existing project.
    Auto {
        project: String,
        #[arg(long)]
        off: bool,
    },
    /// Show one project's outcomes and retained responses.
    Outcomes { project: String },
    /// Bind a project to an existing Git working copy before enabling execution.
    Bind {
        project: String,
        repository: PathBuf,
    },
    /// Open the interactive terminal interface.
    Ui {
        #[arg(long)]
        reduced_motion: bool,
    },
    /// Open Pi with a durable chief identity and the Olympus tool and dashboard.
    Chief {
        #[arg(long)]
        auto: bool,
        #[arg(last = true)]
        pi_args: Vec<String>,
    },
    /// Populate a new isolated home with 20 labelled synthetic projects.
    Demo,
    /// Inspect installed tools without submitting prompts.
    Doctor {
        #[arg(long)]
        firstmate_root: Option<PathBuf>,
    },
    /// Configure the explicit Firstmate home and code root.
    Connect {
        #[arg(long)]
        firstmate_root: PathBuf,
        #[arg(long)]
        firstmate_home: PathBuf,
        #[arg(long)]
        enable_execution: bool,
    },
    /// Read a canonical Firstmate snapshot into the dashboard.
    Observe,
    /// Create, verify or restore a backup. Restore always needs a new home.
    Backup {
        #[command(subcommand)]
        command: BackupCmd,
    },
    /// Print an OS service definition. Does not install or enable it.
    Service {
        #[arg(value_parser=["launchd","systemd"])]
        platform: String,
        #[arg(long)]
        install: bool,
    },
}
#[derive(Subcommand)]
enum BackupCmd {
    Create {
        destination: PathBuf,
    },
    Verify {
        source: PathBuf,
    },
    Restore {
        source: PathBuf,
        destination: PathBuf,
    },
    Offsite {
        source: PathBuf,
    },
}
fn main() {
    if let Err(e) = run() {
        eprintln!("olympus: {e:#}");
        std::process::exit(1)
    }
}
fn run() -> Result<()> {
    let args = Args::parse();
    let home = args.home.unwrap_or_else(home_default);
    let token = || -> Result<String> {
        fs::read_to_string(
            args.token_file
                .as_ref()
                .unwrap_or(&home.join("operator.token")),
        )
        .context("credential missing; run olympus init or pass the assigned --token-file")
    };
    match args.command {
        Cmd::Init => {
            Store::open(&home)?;
            print(json!({"home":home,"initialised":true,"dispatch_enabled":false}));
        }
        Cmd::Start => {
            start(&home)?;
            print(json!({"started":true,"home":home}));
        }
        Cmd::Stop => print(protocol::call(
            &home,
            &token()?,
            &json!({"action":"system.shutdown"}),
        )?),
        Cmd::Status => print(protocol::call(
            &home,
            &token()?,
            &json!({"action":"snapshot"}),
        )?),
        Cmd::Submit {
            text,
            project,
            role,
            complexity,
            new_project,
            task,
            expected_version,
        } => print(protocol::call(
            &home,
            &token()?,
            &json!({"action":"intake.submit","text":text,"project":project,"role":role,"complexity":complexity,"new_project":new_project.is_some(),"name":new_project,"task":task,"expected_version":expected_version,"request_id":id("intake")}),
        )?),
        Cmd::Agenda { view } => print(protocol::call(
            &home,
            &token()?,
            &json!({"action":"agenda","view":view}),
        )?),
        Cmd::Outcomes { project } => print(protocol::call(
            &home,
            &token()?,
            &json!({"action":"project.outcomes","project":project}),
        )?),
        Cmd::Bind {
            project,
            repository,
        } => print(protocol::call(
            &home,
            &token()?,
            &json!({"action":"project.bind","project":project,"repository":repository,"request_id":id("bind")}),
        )?),
        Cmd::Auto { project, off } => {
            start(&home)?;
            if !off {
                let cfg: Value = serde_json::from_slice(
                    &fs::read(home.join("firstmate.json"))
                        .context("Connect an execution-enabled Firstmate home first")?,
                )?;
                if cfg["execute"] != true {
                    bail!("Firstmate connection is observation-only; reconnect with --enable-execution")
                }
                protocol::call(
                    &home,
                    &token()?,
                    &json!({"action":"system.configure","dispatch_enabled":true,"request_id":id("enable")}),
                )?;
            }
            print(protocol::call(
                &home,
                &token()?,
                &json!({"action":"automation.configure","project":project,"enabled":!off,"request_id":id("auto")}),
            )?);
        }
        Cmd::Request { json: input } => {
            let body = if input == "-" {
                let mut s = String::new();
                io::stdin()
                    .take(olympus_core::MAX_REQUEST as u64 + 1)
                    .read_to_string(&mut s)?;
                s
            } else {
                input
            };
            let mut req: Value = serde_json::from_str(&body)?;
            if !req.is_object() {
                bail!("request must be an object")
            }
            if req.get("request_id").is_none() {
                req["request_id"] = json!(id("request"));
            }
            print(protocol::call(&home, &token()?, &req)?);
        }
        Cmd::Chief { auto, pi_args } => {
            start(&home)?;
            let chief_path = home.join("chief.json");
            let existing = fs::read(&chief_path)
                .ok()
                .and_then(|b| serde_json::from_slice::<Value>(&b).ok());
            let chief = if let Some(c) = existing.filter(|c| {
                c["credential_file"]
                    .as_str()
                    .is_some_and(|p| Path::new(p).is_file())
            }) {
                c
            } else {
                let c = protocol::call(
                    &home,
                    &token()?,
                    &json!({"action":"agent.create","role":"chief","harness":"pi","request_id":id("chief")}),
                )?;
                private_write(&chief_path, serde_json::to_string(&c)?.as_bytes())?;
                c
            };
            let credential = chief["credential_file"]
                .as_str()
                .context("chief credential missing")?;
            protocol::call(
                &home,
                &fs::read_to_string(credential)?,
                &json!({"action":"chief.briefing"}),
            )?;
            let exe = std::env::current_exe()?;
            let assets = terminal_assets(&exe)?;
            let bundled =
                assets.join("pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/cli.js");
            let mut cmd = if bundled.is_file() {
                let mut c = Command::new("node");
                c.arg(bundled);
                c
            } else {
                Command::new("pi")
            };
            cmd.arg("-e")
                .arg(assets.join("index.ts"))
                .args(pi_args)
                .env("OLYMPUS_BIN", &exe)
                .env("OLYMPUS_HOME", &home)
                .env("OLYMPUS_CHIEF_MODE", "1")
                .env("OLYMPUS_AGENT_TOKEN_FILE", credential)
                .env("OLYMPUS_AUTO", if auto { "1" } else { "0" });
            let status = cmd.status().context(
                "Pi missing; rerun the installer or install @earendil-works/pi-coding-agent",
            )?;
            if !status.success() {
                bail!("Pi exited with {status}; the coordinator and durable state remain available")
            }
        }
        Cmd::Demo => {
            let mut store = Store::open(&home)?;
            print(demo::seed(&mut store)?);
        }
        Cmd::Doctor { firstmate_root } => print(bridge::doctor(firstmate_root.as_deref())),
        Cmd::Connect {
            firstmate_root,
            firstmate_home,
            enable_execution,
        } => {
            let firstmate_root = firstmate_root.canonicalize()?;
            let firstmate_home = firstmate_home.canonicalize()?;
            if !firstmate_root.join("bin/fm-control.sh").is_file() {
                bail!("Firstmate code root is invalid")
            }
            let store = Store::open(&home)?;
            if !store.authenticate(&token()?)?.is_operator() {
                bail!("operator credential required")
            }
            private_write(&home.join("firstmate.json"),serde_json::to_string_pretty(&json!({"root":firstmate_root,"home":firstmate_home,"execute":enable_execution}))?.as_bytes())?;
            print(
                json!({"connected":true,"execution_enabled":enable_execution,"profiles_still_require_live_verification":true}),
            );
        }
        Cmd::Observe => {
            let cfg: Value = serde_json::from_slice(&fs::read(home.join("firstmate.json"))?)?;
            let snapshot = bridge::observe(
                Path::new(cfg["root"].as_str().context("root")?),
                Path::new(cfg["home"].as_str().context("home")?),
                &home.join("bridge-logs"),
            )?;
            print(protocol::call(
                &home,
                &token()?,
                &json!({"action":"runtime.observe","snapshot":snapshot,"request_id":id("observe")}),
            )?);
        }
        Cmd::Backup { command } => {
            match command {
                BackupCmd::Create { destination } => {
                    let store = Store::open(&home)?;
                    if !store.authenticate(&token()?)?.is_operator() {
                        bail!("operator required")
                    };
                    print(backup::create(&store, &destination)?);
                }
                BackupCmd::Verify { source } => print(backup::verify(&source)?),
                BackupCmd::Restore {
                    source,
                    destination,
                } => print(backup::restore(&source, &destination)?),
                BackupCmd::Offsite { source } => {
                    backup::verify(&source)?;
                    let status=Command::new("restic").arg("backup").arg(&source).status().context("install restic and configure RESTIC_REPOSITORY and a separate password file")?;
                    if !status.success() {
                        bail!("offsite backup failed; local snapshot retained")
                    }
                }
            }
        }
        Cmd::Ui { reduced_motion } => {
            let exe = std::env::current_exe()?;
            let script = terminal_assets(&exe)?.join("terminal.mjs");
            let mut cmd = Command::new("node");
            cmd.arg(script)
                .env("OLYMPUS_BIN", exe)
                .env("OLYMPUS_HOME", &home);
            if let Some(file) = args.token_file {
                cmd.env("OLYMPUS_TOKEN_FILE", file);
            }
            if reduced_motion {
                cmd.env("OLYMPUS_REDUCED_MOTION", "1");
            }
            let status = cmd
                .status()
                .context("Node.js 22+ is required for the terminal interface")?;
            if !status.success() {
                bail!("terminal exited with {status}")
            }
        }
        Cmd::Service { platform, install } => {
            Store::open(&home)?;
            let home = home.canonicalize()?;
            let daemon = std::env::current_exe()?.parent().unwrap().join("olympusd");
            let h = home.to_string_lossy();
            let b = daemon.to_string_lossy();
            let service_path =
                std::env::var("PATH").unwrap_or_else(|_| "/usr/local/bin:/usr/bin:/bin".into());
            if service_path.contains(['\n', '\r', '"', '%']) || h.contains('%') || b.contains('%') {
                bail!("unsupported service environment characters")
            }

            if h.contains(['\n', '\r', '"']) || b.contains(['\n', '\r', '"']) {
                bail!("unsupported service path characters")
            }
            let definition = if platform == "systemd" {
                format!("[Unit]\nDescription=Olympus durable agent coordinator\nAfter=network.target\n\n[Service]\nType=simple\nEnvironment=\"PATH={service_path}\"\nExecStart=\"{b}\" --home \"{h}\"\nRestart=on-failure\nRestartSec=3\nUMask=0077\n\n[Install]\nWantedBy=default.target")
            } else {
                let xml = |s: &str| {
                    s.replace('&', "&amp;")
                        .replace('<', "&lt;")
                        .replace('>', "&gt;")
                };
                format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict><key>Label</key><string>com.olympus.coordinator</string><key>ProgramArguments</key><array><string>{}</string><string>--home</string><string>{}</string></array><key>EnvironmentVariables</key><dict><key>PATH</key><string>{}</string></dict><key>KeepAlive</key><true/><key>RunAtLoad</key><true/><key>ThrottleInterval</key><integer>3</integer></dict></plist>",xml(&b),xml(&h),xml(&service_path))
            };
            if install {
                install_service(&platform, &home, &definition, &token()?)?;
            } else {
                println!("{definition}");
            }
        }
    }
    Ok(())
}
fn print(v: Value) {
    println!("{}", serde_json::to_string_pretty(&v).unwrap());
}
fn start(home: &Path) -> Result<()> {
    Store::open(home)?;
    let token = fs::read_to_string(home.join("operator.token"))?;
    if protocol::call(home, &token, &json!({"action":"snapshot"})).is_ok() {
        return Ok(());
    }
    private_dir(home)?;
    let log = home.join("coordinator.log");
    if !log.exists() {
        private_write(&log, b"")?;
    }
    let file = fs::OpenOptions::new().append(true).open(log)?;
    let daemon = std::env::current_exe()?.parent().unwrap().join("olympusd");
    let mut command = Command::new(daemon);
    command
        .arg("--home")
        .arg(home)
        .stdin(Stdio::null())
        .stdout(file.try_clone()?)
        .stderr(file);
    // Detach from the invoking terminal session so hangup cannot stop the coordinator.
    // setsid is async-signal-safe and this closure allocates no memory on success.
    use std::os::unix::process::CommandExt;
    unsafe {
        command.pre_exec(|| {
            if libc::setsid() == -1 {
                Err(std::io::Error::last_os_error())
            } else {
                Ok(())
            }
        });
    }
    let mut child = command.spawn()?;
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if protocol::call(home, &token, &json!({"action":"snapshot"})).is_ok() {
            return Ok(());
        };
        if let Some(s) = child.try_wait()? {
            bail!("coordinator exited {s}; inspect coordinator.log")
        };
        if Instant::now() > deadline {
            bail!("coordinator has not become ready; inspect coordinator.log before retrying")
        };
        thread::sleep(Duration::from_millis(100));
    }
}

fn terminal_assets(exe: &Path) -> Result<PathBuf> {
    let parent = exe.parent().context("binary directory")?;
    if parent.join("index.ts").is_file() {
        return Ok(parent.to_path_buf());
    }
    let installed = parent.join("../lib/olympus");
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../packages/olympus-pi");
    let path = if installed.join("index.ts").is_file() {
        installed
    } else {
        dev
    };
    if !path.join("index.ts").is_file() {
        bail!("terminal assets missing; reinstall Olympus")
    }
    Ok(path)
}

fn install_service(platform: &str, home: &Path, definition: &str, token: &str) -> Result<()> {
    let user_home =
        PathBuf::from(std::env::var_os("HOME").context("HOME required for a user service")?);
    let destination = if platform == "systemd" {
        user_home.join(".config/systemd/user/olympus.service")
    } else {
        user_home.join("Library/LaunchAgents/com.olympus.coordinator.plist")
    };
    private_dir(destination.parent().unwrap())?;
    private_write(&destination, definition.as_bytes())?;
    if protocol::call(home, token, &json!({"action":"snapshot"})).is_ok() {
        protocol::call(home, token, &json!({"action":"system.shutdown"}))?;
        let until = Instant::now() + Duration::from_secs(6);
        while home.join("endpoint.json").exists() && Instant::now() < until {
            thread::sleep(Duration::from_millis(50));
        }
        if home.join("endpoint.json").exists() {
            bail!("coordinator has not stopped; service file saved, activation deferred")
        }
    }
    let checked = |command: &mut Command| -> Result<()> {
        let status = command.status()?;
        if !status.success() {
            bail!(
                "user service command failed: {status}; definition retained at {}",
                destination.display()
            )
        }
        Ok(())
    };
    if platform == "systemd" {
        checked(Command::new("systemctl").args(["--user", "daemon-reload"]))?;
        checked(Command::new("systemctl").args(["--user", "enable", "--now", "olympus.service"]))?;
    } else {
        let domain = format!("gui/{}", unsafe { libc::getuid() });
        let _ = Command::new("launchctl")
            .args(["bootout", &format!("{domain}/com.olympus.coordinator")])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        checked(
            Command::new("launchctl")
                .args(["bootstrap", &domain])
                .arg(&destination),
        )?;
    }
    print(json!({"installed":destination,"automatic_restart":true,"scope":"current user"}));
    Ok(())
}

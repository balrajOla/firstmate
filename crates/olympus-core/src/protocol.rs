use crate::*;
use std::{
    io::{BufRead, BufReader, Read, Write},
    os::unix::net::UnixStream,
};

pub enum Stream {
    Unix(UnixStream),
    Tcp(std::net::TcpStream),
}
impl Read for Stream {
    fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
        match self {
            Self::Unix(s) => s.read(b),
            Self::Tcp(s) => s.read(b),
        }
    }
}
impl Write for Stream {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        match self {
            Self::Unix(s) => s.write(b),
            Self::Tcp(s) => s.write(b),
        }
    }
    fn flush(&mut self) -> std::io::Result<()> {
        match self {
            Self::Unix(s) => s.flush(),
            Self::Tcp(s) => s.flush(),
        }
    }
}
impl Stream {
    pub fn timeout(&self, read: Duration, write: Duration) -> Result<()> {
        match self {
            Self::Unix(s) => {
                s.set_read_timeout(Some(read))?;
                s.set_write_timeout(Some(write))?;
            }
            Self::Tcp(s) => {
                s.set_read_timeout(Some(read))?;
                s.set_write_timeout(Some(write))?;
            }
        }
        Ok(())
    }
}

pub fn call(home: &Path, token: &str, request: &Value) -> Result<Value> {
    let mut socket = if home.join("endpoint.json").exists() {
        let endpoint: Value = serde_json::from_slice(&fs::read(home.join("endpoint.json"))?)?;
        if endpoint["transport"] == "tcp" {
            let address: std::net::SocketAddr = endpoint["address"]
                .as_str()
                .context("endpoint address")?
                .parse()?;
            if address.ip() != std::net::IpAddr::V4(std::net::Ipv4Addr::LOCALHOST) {
                bail!("controller endpoint must be loopback-only")
            }
            Stream::Tcp(
                std::net::TcpStream::connect_timeout(&address, Duration::from_secs(2))
                    .context("coordinator unavailable; run olympus start")?,
            )
        } else {
            Stream::Unix(
                UnixStream::connect(home.join("olympus.sock"))
                    .context("coordinator unavailable; run olympus start")?,
            )
        }
    } else {
        Stream::Unix(
            UnixStream::connect(home.join("olympus.sock"))
                .context("coordinator unavailable; run olympus start")?,
        )
    };
    socket.timeout(Duration::from_secs(20), Duration::from_secs(5))?;
    let wire = json!({"schema":SCHEMA,"token":token.trim(),"request":request});
    let bytes = serde_json::to_vec(&wire)?;
    if bytes.len() > MAX_REQUEST {
        bail!("request too large")
    };
    socket.write_all(&bytes)?;
    socket.write_all(b"\n")?;
    let mut line = String::new();
    BufReader::new(socket)
        .take((MAX_REQUEST * 8) as u64)
        .read_line(&mut line)?;
    let reply: Value = serde_json::from_str(&line).context("invalid coordinator response")?;
    if reply["ok"] != true {
        bail!(
            "{}",
            reply["error"]
                .as_str()
                .unwrap_or("coordinator rejected request")
        )
    }
    Ok(reply["result"].clone())
}

pub fn serve_connection(mut socket: Stream, home: &Path) -> Result<bool> {
    socket.timeout(Duration::from_secs(3), Duration::from_secs(3))?;
    let mut line = String::new();
    BufReader::new(&mut socket)
        .take((MAX_REQUEST + 1) as u64)
        .read_line(&mut line)?;
    let mut shutdown = false;
    let result = (|| -> Result<Value> {
        if line.len() > MAX_REQUEST || !line.ends_with('\n') {
            bail!("invalid or oversized request frame")
        }
        let req: Value = serde_json::from_str(&line)?;
        if req["schema"] != SCHEMA {
            bail!("unsupported protocol schema")
        }
        let mut store = Store::open(home)?;
        let principal = store.authenticate(&required(&req, "token")?)?;
        let request = req.get("request").context("request required")?;
        if request["action"] == "system.shutdown" {
            require_operator(&principal)?;
            shutdown = true;
            return Ok(json!({"stopping":true,"worker_workspaces_preserved":true}));
        }
        store.execute(&principal, request)
    })();
    let reply = match result {
        Ok(r) => json!({"ok":true,"result":r}),
        Err(e) => json!({"ok":false,"error":e.to_string()}),
    };
    socket.write_all(serde_json::to_string(&reply)?.as_bytes())?;
    socket.write_all(b"\n")?;
    Ok(shutdown)
}

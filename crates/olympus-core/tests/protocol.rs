use olympus_core::{protocol, Store, MAX_REQUEST, SCHEMA};
use serde_json::{json, Value};
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    net::{TcpListener, TcpStream},
    os::unix::net::{UnixListener, UnixStream},
    sync::mpsc,
    thread,
    time::Duration,
};
use tempfile::TempDir;

fn check_nonblocking_server(mut client: protocol::Stream, server: protocol::Stream) {
    client
        .timeout(Duration::from_secs(2), Duration::from_secs(2))
        .unwrap();
    let home = TempDir::new().unwrap();
    let store = Store::open(home.path()).unwrap();
    let token = fs::read_to_string(home.path().join("operator.token")).unwrap();
    drop(store);
    let directory = home.path().to_path_buf();
    let (ready, started) = mpsc::channel();
    let serving = thread::spawn(move || {
        ready.send(()).unwrap();
        protocol::serve_connection(server, &directory)
    });
    started.recv_timeout(Duration::from_secs(1)).unwrap();
    // An accepted BSD socket inherits the nonblocking listener flag. The
    // protocol must still wait for a request using its configured deadline.
    thread::sleep(Duration::from_millis(50));
    let request = json!({"schema":SCHEMA,"token":token.trim(),"request":{"action":"snapshot"}});
    let sent = client.write_all(format!("{request}\n").as_bytes());
    let mut response = String::new();
    let received = BufReader::new(client).read_line(&mut response);
    let served = serving.join().unwrap();
    assert!(
        served.is_ok(),
        "nonblocking accepted stream failed: {served:?}"
    );
    sent.unwrap();
    received.unwrap();
    assert!(response.ends_with('\n'));
    let response: Value = serde_json::from_str(&response).unwrap();
    assert_eq!(response["ok"], true);
    assert_eq!(response["result"]["schema"], SCHEMA);
}

#[test]
fn nonblocking_accepted_unix_stream_uses_blocking_protocol_deadlines() {
    let (client, server) = UnixStream::pair().unwrap();
    server.set_nonblocking(true).unwrap();
    check_nonblocking_server(
        protocol::Stream::Unix(client),
        protocol::Stream::Unix(server),
    );
}

#[test]
fn nonblocking_accepted_tcp_stream_uses_blocking_protocol_deadlines() {
    let listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).unwrap();
    let client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    let (server, _) = listener.accept().unwrap();
    server.set_nonblocking(true).unwrap();
    check_nonblocking_server(protocol::Stream::Tcp(client), protocol::Stream::Tcp(server));
}

fn fake_reply(chunks: Vec<Vec<u8>>) -> anyhow::Result<Value> {
    let home = TempDir::new().unwrap();
    let listener = UnixListener::bind(home.path().join("olympus.sock")).unwrap();
    let responding = thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        socket
            .set_write_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut request = String::new();
        BufReader::new(&mut socket).read_line(&mut request).unwrap();
        for chunk in chunks {
            if socket.write_all(&chunk).is_err() {
                break;
            }
            thread::sleep(Duration::from_millis(2));
        }
    });
    let result = protocol::call(
        home.path(),
        "synthetic-protocol-token",
        &json!({"action":"snapshot"}),
    );
    responding.join().unwrap();
    result
}

#[test]
fn client_reads_fragmented_response_larger_than_socket_buffer() {
    let body = "x".repeat(32 * 1024);
    let response = format!("{}\n", json!({"ok":true,"result":{"body":body}}));
    let chunks = response.as_bytes().chunks(997).map(Vec::from).collect();
    let received = fake_reply(chunks).unwrap();
    assert_eq!(received["body"], body);
}

#[test]
fn client_rejects_valid_json_without_complete_response_frame() {
    let result = fake_reply(vec![br#"{"ok":true,"result":{"unfinished":true}}"#.to_vec()]);
    assert!(
        result.is_err(),
        "unterminated reply was accepted: {result:?}"
    );
}

#[test]
fn client_rejects_response_larger_than_limit() {
    let response = format!(
        "{}\n",
        json!({"ok":true,"result":{"body":"x".repeat(MAX_REQUEST * 8)}})
    );
    let result = fake_reply(vec![response.into_bytes()]);
    assert!(result.is_err(), "oversized reply was accepted");
}

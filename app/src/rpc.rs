//! The Local core's client (ADR 0013): line-delimited JSON-RPC 2.0 on its Unix socket. One connection carries
//! calls and, after `events.subscribe`, the core's events. Answers are matched to calls by id.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{Value, json};

pub struct Core {
    writer: Mutex<UnixStream>,
    pending: Arc<Mutex<HashMap<u64, async_channel::Sender<Value>>>>,
    next: AtomicU64,
}

/// `$XDG_RUNTIME_DIR/comms-terminal/core.sock`, as `core.default_listen` puts it (`/tmp/comms-terminal-<uid>`
/// without one: the owner of `$HOME` is this user).
pub fn socket_path() -> PathBuf {
    use std::os::unix::fs::MetadataExt;
    let base = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).unwrap_or_else(|| {
        let uid = std::env::var_os("HOME").and_then(|h| std::fs::metadata(h).ok()).map_or(0, |m| m.uid());
        PathBuf::from(format!("/tmp/comms-terminal-{uid}"))
    });
    base.join("comms-terminal").join("core.sock")
}

impl Core {
    /// Connects to the core, starting it with `command` when nothing answers (D-E: the App starts the core;
    /// the core outlives the App). Core events go to `events`.
    pub fn connect(path: &Path, command: &[String], events: async_channel::Sender<Value>) -> anyhow::Result<Arc<Core>> {
        let stream = match UnixStream::connect(path) {
            Ok(s) => s,
            Err(_) => {
                let (program, args) = command.split_first().ok_or_else(|| anyhow::anyhow!("no core command"))?;
                Command::new(program)
                    .args(args)
                    .args(["core", "--listen"])
                    .arg(path)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .map_err(|e| anyhow::anyhow!("cannot start the Local core ({program}): {e}"))?;
                let mut tries = 0;
                loop {
                    match UnixStream::connect(path) {
                        Ok(s) => break s,
                        Err(e) if tries > 600 => anyhow::bail!("the Local core did not start: {e}"),
                        Err(_) => {
                            tries += 1;
                            std::thread::sleep(Duration::from_millis(100));
                        }
                    }
                }
            }
        };
        let reader = BufReader::new(stream.try_clone()?);
        let pending: Arc<Mutex<HashMap<u64, async_channel::Sender<Value>>>> = Arc::default();
        let core = Arc::new(Core { writer: Mutex::new(stream), pending: pending.clone(), next: AtomicU64::new(1) });
        std::thread::spawn(move || {
            for line in reader.lines() {
                let Ok(line) = line else { break };
                let Ok(message) = serde_json::from_str::<Value>(&line) else { continue };
                if message.get("method").and_then(Value::as_str) == Some("event") {
                    let _ = events.send_blocking(message["params"].clone());
                } else if let Some(id) = message.get("id").and_then(Value::as_u64) {
                    if let Some(answer) = pending.lock().unwrap().remove(&id) {
                        let _ = answer.send_blocking(message);
                    }
                }
            }
            let _ = events.send_blocking(json!({"kind": "disconnected", "data": null}));
        });
        Ok(core)
    }

    /// Sends one call and returns the channel its answer arrives on.
    pub fn send(&self, method: &str, params: Value) -> anyhow::Result<async_channel::Receiver<Value>> {
        let id = self.next.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = async_channel::bounded(1);
        self.pending.lock().unwrap().insert(id, tx);
        let line = json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}).to_string() + "\n";
        self.writer.lock().unwrap().write_all(line.as_bytes())?;
        Ok(rx)
    }

    /// One call, answered: the result, or the error's message.
    pub async fn call(self: Arc<Self>, method: String, params: Value) -> Result<Value, String> {
        let rx = self.send(&method, params).map_err(|e| format!("Local core: {e}"))?;
        let answer = rx.recv().await.map_err(|_| "Local core went away".to_string())?;
        match answer.get("error") {
            Some(error) => Err(error["message"].as_str().unwrap_or("error").to_string()),
            None => Ok(answer["result"].clone()),
        }
    }

    /// The same, blocking: for start-up, before the window's executor runs.
    pub fn call_blocking(&self, method: &str, params: Value) -> Result<Value, String> {
        let rx = self.send(method, params).map_err(|e| format!("Local core: {e}"))?;
        let answer = rx.recv_blocking().map_err(|_| "Local core went away".to_string())?;
        match answer.get("error") {
            Some(error) => Err(error["message"].as_str().unwrap_or("error").to_string()),
            None => Ok(answer["result"].clone()),
        }
    }
}

//! `comms-terminal-app shot [file.png]`, `status` and `do <command>`: drive the running window from a shell
//! (agents, tests) over `<home>/comms-terminal-app.sock`, one JSON line each way. As herdr-app's `control.rs`.

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{Value, json};

use crate::host::Request;
use crate::workspace;

fn socket() -> PathBuf {
    workspace::home().join("comms-terminal-app.sock")
}

/// Serves the socket for this window. A live socket means another window owns this home.
pub fn listen(tx: async_channel::Sender<Request>) {
    let path = socket();
    if UnixStream::connect(&path).is_ok() {
        return;
    }
    let _ = std::fs::remove_file(&path);
    let Ok(listener) = UnixListener::bind(&path) else { return };
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let tx = tx.clone();
            std::thread::spawn(move || serve(stream, tx));
        }
    });
}

fn serve(stream: UnixStream, tx: async_channel::Sender<Request>) {
    let mut line = String::new();
    if BufReader::new(&stream).read_line(&mut line).is_err() {
        return;
    }
    let reply = match serde_json::from_str::<Value>(&line) {
        Ok(cmd) => {
            let seconds = if cmd.get("probe").is_some() { 600 } else if cmd.get("shot").is_some() { 30 } else { 5 };
            let (reply_tx, reply_rx) = std::sync::mpsc::channel();
            let _ = tx.send_blocking(Request::Control { cmd, reply: reply_tx });
            reply_rx.recv_timeout(Duration::from_secs(seconds)).unwrap_or_else(|_| json!({"error": "the window did not answer"}))
        }
        Err(e) => json!({"error": format!("bad request: {e}")}),
    };
    let _ = (&stream).write_all(format!("{reply}\n").as_bytes());
}

/// One App per home: a second launch brings the running window forward and quits.
pub fn raise_running() -> bool {
    let Ok(mut stream) = UnixStream::connect(socket()) else { return false };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
    if stream.write_all(format!("{}\n", json!({"raise": true})).as_bytes()).is_ok() {
        let _ = BufReader::new(&stream).read_line(&mut String::new());
    }
    true
}

/// The client side; returns the process exit code.
pub fn client(args: &[String]) -> i32 {
    let cmd = match args {
        [verb, rest @ ..] if verb == "shot" && rest.len() <= 1 => {
            let path = PathBuf::from(rest.first().map(String::as_str).unwrap_or("comms-terminal-shot.png"));
            let Ok(path) = std::path::absolute(path) else { return fail("cannot resolve the screenshot path") };
            if path.extension().is_none_or(|e| !e.eq_ignore_ascii_case("png")) {
                return fail("the screenshot path must end in .png");
            }
            json!({"shot": path})
        }
        [verb] if verb == "status" => json!({"status": true}),
        [verb, command] if verb == "do" => json!({"do": command}),
        [verb, rest @ ..] if verb == "probe" => {
            let moves = rest.first().and_then(|n| n.parse::<u64>().ok()).unwrap_or(200);
            let view = rest.get(1).map(String::as_str).unwrap_or("done");
            json!({"probe": {"moves": moves, "view": view}})
        }
        _ => return fail("usage: comms-terminal-app status | shot [file.png] | do <command> | probe [moves] [view]"),
    };
    let mut stream = match UnixStream::connect(socket()) {
        Ok(s) => s,
        Err(e) => return fail(&format!("no Comms Terminal App window at {}: {e}", socket().display())),
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(605)));
    let mut line = String::new();
    if stream.write_all(format!("{cmd}\n").as_bytes()).is_err() || BufReader::new(&stream).read_line(&mut line).is_err() {
        return fail("the window hung up");
    }
    let reply: Value = serde_json::from_str(&line).unwrap_or(Value::Null);
    if let Some(error) = reply.get("error") {
        return fail(error.as_str().unwrap_or("failed"));
    }
    match reply.get("path").and_then(Value::as_str) {
        Some(path) => println!("{path}"),
        None => println!("{}", serde_json::to_string_pretty(&reply).unwrap_or_default()),
    }
    0
}

/// The last rendered frame as a PNG, written beside its destination and renamed, so a failed capture never
/// leaves half a file. Works when the window is covered or minimized; needs no screen-recording permission.
pub fn save_shot(window: &gpui::Window, path: &Path) -> anyhow::Result<()> {
    let image = window.render_to_image()?;
    let mut bytes = Vec::new();
    image::DynamicImage::ImageRgba8(image).write_to(&mut std::io::Cursor::new(&mut bytes), image::ImageFormat::Png)?;
    let temporary = path.with_file_name(format!(".comms-terminal-shot-{}.png", std::process::id()));
    let result = std::fs::write(&temporary, &bytes).and_then(|_| std::fs::rename(&temporary, path));
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    Ok(result?)
}

fn fail(message: &str) -> i32 {
    eprintln!("comms-terminal-app: {message}");
    2
}

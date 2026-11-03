//! The App's diagnostic log: one JSON line per key, command, toast, status change and error, so an agent can answer
//! "why did that key do nothing" from the file. It lives in `<home>/data/log.jsonl` (the file watcher ignores
//! `data`) and keeps about a day: once the file's first line is 24 hours old it becomes `log.1.jsonl`, replacing the
//! one before, so 24 to 48 hours are on disk. Text typed into a field is logged as "<text>", never the letters.
use serde_json::{Value, json};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

const DAY_MS: u64 = 24 * 60 * 60 * 1000;

struct Log {
    path: PathBuf,
    started: u64, // the current file's first line, in Unix milliseconds
}

static LOG: Mutex<Option<Log>> = Mutex::new(None);

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_millis() as u64)
}

/// The log file for `home`, for `status`.
pub fn path(home: &Path) -> PathBuf {
    home.join("data").join("log.jsonl")
}

/// Opens the log under `home`; without it, `write` does nothing.
pub fn open(home: &Path) {
    let path = path(home);
    let _ = std::fs::create_dir_all(path.parent().unwrap_or(home));
    let started = std::fs::File::open(&path)
        .ok()
        .and_then(|f| std::io::BufReader::new(f).lines().next()?.ok())
        .and_then(|line| serde_json::from_str::<Value>(&line).ok()?["t"].as_u64())
        .unwrap_or_else(now_ms);
    *LOG.lock().unwrap() = Some(Log { path, started });
}

/// Appends one entry: `{"t": <Unix ms>, "kind": kind, ...fields}`.
pub fn write(kind: &str, fields: Value) {
    let t = now_ms();
    let mut entry = json!({"t": t, "kind": kind});
    if let (Some(e), Value::Object(f)) = (entry.as_object_mut(), fields) {
        e.extend(f);
    }
    let mut guard = LOG.lock().unwrap();
    let Some(log) = guard.as_mut() else { return };
    if t.saturating_sub(log.started) >= DAY_MS {
        let _ = std::fs::rename(&log.path, log.path.with_file_name("log.1.jsonl"));
        log.started = t;
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(&log.path) {
        let _ = writeln!(file, "{entry}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Entries append as JSON lines; a file whose first line is a day old moves to log.1.jsonl.
    #[test]
    fn appends_and_keeps_about_a_day() {
        let home = std::env::temp_dir().join(format!("ct-app-log-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(home.join("data")).unwrap();
        std::fs::write(path(&home), format!("{}\n", json!({"t": now_ms() - DAY_MS - 1, "kind": "old"}))).unwrap();
        open(&home);
        write("key", json!({"key": "e"}));
        let old = std::fs::read_to_string(home.join("data").join("log.1.jsonl")).unwrap();
        assert!(old.contains("\"old\""), "{old}");
        let now = std::fs::read_to_string(path(&home)).unwrap();
        let line: Value = serde_json::from_str(now.trim()).unwrap();
        assert_eq!((line["kind"].as_str(), line["key"].as_str()), (Some("key"), Some("e")));
        std::fs::remove_dir_all(&home).unwrap();
    }
}

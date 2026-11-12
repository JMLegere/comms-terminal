//! The `comms` module every view and Panel imports (ADR 0013). Each loaded copy gets its own instance: its Origin
//! (`view` for the App's shipped views, `panel` for Interface-file Panels), and its own queues for the core's
//! events and for key commands. Shared state lives here so one view's selection can drive another's.

use std::cell::RefCell;
use std::sync::{Arc, Mutex, OnceLock};

use gpui_shell::{HostError, HostModule, HostValue};
use serde_json::{Map, Value, json};

use crate::rpc::Core;

pub const DECLARATIONS: &str = include_str!("../defaults/comms.d.ts");

static CORE: OnceLock<Arc<Core>> = OnceLock::new();
static REQUESTS: OnceLock<async_channel::Sender<Request>> = OnceLock::new();
static LISTENERS: Mutex<Vec<async_channel::Sender<Value>>> = Mutex::new(Vec::new());
static FONTS: OnceLock<Vec<String>> = OnceLock::new(); // installed font families, for Settings › Look
/// Probe marks (`comms-terminal-app probe`): (name, when), kept only while a probe runs.
pub static MARKS: Mutex<Option<Vec<(String, std::time::Instant)>>> = Mutex::new(None);

thread_local! {
    static SHARED: RefCell<Map<String, Value>> = RefCell::new(Map::new());
    static STATUS: RefCell<Value> = const { RefCell::new(Value::Null) };
    static THEME: RefCell<Value> = RefCell::new(  // the shipped theme until settings choose one (and in `check`)
        serde_json::from_str(include_str!("../defaults/themes/gruvbox-material-light.json")).unwrap_or(Value::Null));
}

/// What a script asks the window to do.
pub enum Request {
    /// Shared state changed: every view renders again.
    Refresh,
    /// Run an App command (`app.reset`, `<view>.<command>`).
    Run(String),
    /// Open a web link or an Attachment's signed link in the default browser (FR-9).
    Open(String),
    /// Give the keyboard back to the window, out of any text field.
    Focus,
    /// Paint again: a message body finished laying out or painting a tile (`body.rs`).
    Repaint,
    /// The launcher's staging of the Service's build moved on: `ready` or `failed` (`window.rs` `prepare_update`).
    UpdateStep(&'static str),
    /// How far the staging is: `{elapsed, percent, left}` in seconds (percent and left null before a first build).
    UpdateProgress(Value),
    /// A control-socket request (`control.rs`) and where its answer goes.
    Control { cmd: Value, reply: std::sync::mpsc::Sender<Value> },
}

pub fn mark(name: &str) {
    if let Some(marks) = MARKS.lock().unwrap().as_mut() {
        marks.push((name.to_owned(), std::time::Instant::now()));
    }
}

/// The installed font families, captured once at start (Settings › Look lists them).
pub fn set_fonts(mut fonts: Vec<String>) {
    fonts.sort_unstable_by_key(|f| f.to_lowercase());
    fonts.dedup();
    let _ = FONTS.set(fonts);
}

/// Writes one key of `settings.json` (null removes it). The file watcher then reloads the look, so a change applies
/// at once (Settings: no Save step, as herdr-app's).
pub fn write_setting(key: &str, value: Value) -> Result<(), String> {
    let home = crate::workspace::home();
    let mut settings = crate::workspace::read_json(&home, "settings.json").unwrap_or_else(|_| json!({}));
    let Some(map) = settings.as_object_mut() else { return Err("settings.json is not an object".into()) };
    if value.is_null() {
        map.remove(key);
    } else {
        map.insert(key.to_owned(), value);
    }
    let text = serde_json::to_string_pretty(&settings).map_err(|e| e.to_string())?;
    std::fs::write(home.join("settings.json"), text + "\n").map_err(|e| e.to_string())
}

/// The newest `n` diagnostic log entries (Settings › Diagnostics), oldest first; reads only the file's last 512 KiB.
pub fn log_tail(n: usize) -> Vec<Value> {
    use std::io::{Read, Seek, SeekFrom};
    let Ok(mut file) = std::fs::File::open(crate::log::path(&crate::workspace::home())) else { return Vec::new() };
    let len = file.metadata().map_or(0, |m| m.len());
    let _ = file.seek(SeekFrom::Start(len.saturating_sub(512 * 1024)));
    let mut text = String::new();
    let _ = file.read_to_string(&mut text);
    let lines: Vec<Value> = text.lines().filter_map(|l| serde_json::from_str(l).ok()).collect();
    lines[lines.len().saturating_sub(n)..].to_vec()
}

pub fn install(core: Arc<Core>, requests: async_channel::Sender<Request>) {
    let _ = CORE.set(core);
    let _ = REQUESTS.set(requests);
}

/// Sends a request to the window from any thread; false before the window runs (as in `check`).
pub fn request(request: Request) -> bool {
    REQUESTS.get().is_some_and(|tx| tx.try_send(request).is_ok())
}

pub fn core() -> Option<Arc<Core>> {
    CORE.get().cloned()
}

pub fn shared(key: &str) -> Value {
    SHARED.with(|s| s.borrow().get(key).cloned().unwrap_or(Value::Null))
}

/// Sets a shared value from the host (the window's width before each render); views read it with `shared()`.
pub fn set_shared(key: &str, value: Value) {
    SHARED.with(|s| s.borrow_mut().insert(key.to_owned(), value));
}

/// A toast from the host, as a view's `toast()` (and logged the same way).
pub fn toast(message: &str) {
    crate::log::write("toast", json!({"message": message, "severity": "information"}));
    broadcast(&json!({"kind": "toast", "data": {"message": message, "severity": "information", "timeout": null}}));
}

pub fn status() -> Value {
    STATUS.with(|s| s.borrow().clone())
}

pub fn set_status(status: Value) {
    STATUS.with(|s| *s.borrow_mut() = status);
}

pub fn theme_value(token: &str) -> Option<String> {
    THEME.with(|t| t.borrow().get(token).and_then(Value::as_str).map(str::to_owned))
}

pub fn set_theme(theme: Value) {
    THEME.with(|t| *t.borrow_mut() = theme);
}

/// Hands one core event to every loaded copy waiting in `nextEvent()`; a copy that went away is dropped.
pub fn broadcast(event: &Value) {
    LISTENERS.lock().unwrap().retain(|tx| tx.try_send(event.clone()).is_ok() || !tx.is_closed());
}

pub fn to_host(v: &Value) -> HostValue {
    match v {
        Value::Null => HostValue::Null,
        Value::Bool(b) => HostValue::Bool(*b),
        Value::Number(n) => HostValue::Number(n.as_f64().unwrap_or(0.0)),
        Value::String(s) => HostValue::Str(s.clone()),
        Value::Array(a) => HostValue::Array(a.iter().map(to_host).collect()),
        Value::Object(o) => HostValue::Object(o.iter().map(|(k, v)| (k.clone(), to_host(v))).collect()),
    }
}

pub fn from_host(v: &HostValue) -> Value {
    match v {
        HostValue::Null => Value::Null,
        HostValue::Bool(b) => Value::Bool(*b),
        HostValue::Number(n) if n.fract() == 0.0 && n.abs() < 9e15 => Value::from(*n as i64),
        HostValue::Number(n) => serde_json::Number::from_f64(*n).map_or(Value::Null, Value::Number),
        HostValue::Str(s) => Value::String(s.clone()),
        HostValue::Array(a) => Value::Array(a.iter().map(from_host).collect()),
        HostValue::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), from_host(v))).collect()),
    }
}

/// The module for one copy. `commands` carries the key commands addressed to it (`<view>.<command>`).
pub fn module(origin: &'static str, commands: async_channel::Receiver<String>) -> HostModule {
    let (events_tx, events) = async_channel::bounded::<Value>(256);
    LISTENERS.lock().unwrap().push(events_tx);
    HostModule::new("comms")
        .declarations(DECLARATIONS)
        .async_function("call", move |args| {
            let method = args.string(0)?.to_owned();
            let mut params = match args.get(1).map(from_host) {
                None | Some(Value::Null) => Map::new(),
                Some(Value::Object(m)) => m,
                Some(_) => return Err(HostError::new("call(method, params): params must be an object")),
            };
            params.insert("origin".into(), json!(origin)); // the host decides the Origin, never the script
            let core = CORE.get().cloned().ok_or_else(|| HostError::new("no Local core"))?;
            Ok(Box::pin(async move {
                core.call(method, Value::Object(params)).await.map(|v| to_host(&v)).map_err(HostError::new)
            }))
        })
        .async_function("nextEvent", move |_| {
            let events = events.clone();
            Ok(Box::pin(async move {
                events.recv().await.map(|v| to_host(&v)).map_err(|_| HostError::new("closed"))
            }))
        })
        .async_function("nextCommand", move |_| {
            let commands = commands.clone();
            Ok(Box::pin(async move {
                commands.recv().await.map(HostValue::Str).map_err(|_| HostError::new("closed"))
            }))
        })
        .function("status", |_| Ok(STATUS.with(|s| to_host(&s.borrow()))))
        .function("theme", |_| Ok(THEME.with(|t| to_host(&t.borrow()))))
        .function("origin", move |_| Ok(HostValue::Str(origin.to_owned())))
        .function("shared", |args| {
            let key = args.string(0)?;
            Ok(SHARED.with(|s| s.borrow().get(key).map(to_host).unwrap_or(HostValue::Null)))
        })
        .function("setShared", |args| {
            let key = args.string(0)?.to_owned();
            let value = args.get(1).map(from_host).unwrap_or(Value::Null);
            let changed = SHARED.with(|s| s.borrow_mut().insert(key, value.clone()).as_ref() != Some(&value));
            if changed {
                if let Some(tx) = REQUESTS.get() {
                    let _ = tx.try_send(Request::Refresh);
                }
            }
            Ok(HostValue::Bool(changed))
        })
        .function("mark", |args| {
            mark(args.string(0)?);
            Ok(HostValue::Null)
        })
        .function("open", |args| {
            let url = args.string(0)?.to_owned();
            if let Some(tx) = REQUESTS.get() {
                let _ = tx.try_send(Request::Open(url));
            }
            Ok(HostValue::Null)
        })
        .function("reveal", |args| {
            // FR-A5: save a stored Attachment in Downloads and select it in the file manager, off the UI thread.
            let url = args.string(0)?.to_owned();
            let name = args.string(1)?.to_owned();
            std::thread::spawn(move || {
                let event = match crate::reveal::reveal(&url, &name) {
                    Ok(path) => {
                        crate::log::write("reveal", json!({"name": name}));
                        json!({"message": format!("Saved to {}", path.display()), "severity": "information"})
                    }
                    Err(e) => json!({"message": format!("Could not open {name}: {e:#}"), "severity": "error"}),
                };
                broadcast(&json!({"kind": "toast", "data": event}));
            });
            Ok(HostValue::Null)
        })
        .function("toast", |args| {
            let message = args.string(0)?.to_owned();
            let severity = args.get(1).map(from_host).and_then(|v| v.as_str().map(str::to_owned)).unwrap_or("information".into());
            let timeout = args.get(2).map(from_host).and_then(|v| v.as_f64()); // seconds shown; the toasts view's default otherwise
            crate::log::write("toast", json!({"message": message, "severity": severity}));
            broadcast(&json!({"kind": "toast", "data": {"message": message, "severity": severity, "timeout": timeout}}));
            Ok(HostValue::Null)
        })
        .function("fonts", |_| Ok(to_host(&json!(FONTS.get().cloned().unwrap_or_default()))))
        .function("setting", |args| {
            let key = args.string(0)?.to_owned();
            let value = args.get(1).map(from_host).unwrap_or(Value::Null);
            write_setting(&key, value).map_err(|e| HostError::new(&e))?;
            Ok(HostValue::Bool(true))
        })
        .function("settings", |_| {
            Ok(to_host(&crate::workspace::read_json(&crate::workspace::home(), "settings.json").unwrap_or(Value::Null)))
        })
        .function("logTail", |args| {
            let n = args.get(0).map(from_host).and_then(|v| v.as_u64()).unwrap_or(50) as usize;
            Ok(to_host(&Value::Array(log_tail(n.min(5000)))))
        })
        .function("keys", |_| {
            let keys = crate::workspace::read_json(&crate::workspace::home(), "keys.json").unwrap_or(Value::Null);
            Ok(to_host(&keys))
        })
        .function("moduleOf", |args| {
            Ok(crate::window::module_of(args.string(0)?).map_or(HostValue::Null, |m| to_host(&Value::String(m.into()))))
        })
        .function("focusMain", |_| {
            if let Some(tx) = REQUESTS.get() {
                let _ = tx.try_send(Request::Focus);
            }
            Ok(HostValue::Null)
        })
        .function("run", |args| {
            let command = args.string(0)?.to_owned();
            if let Some(tx) = REQUESTS.get() {
                let _ = tx.try_send(Request::Run(command));
            }
            Ok(HostValue::Null)
        })
}

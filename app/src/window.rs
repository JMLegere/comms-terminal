//! The App's window: the layout's blocks, each a loaded view or Panel (gpui-shell), the Local core's events, key
//! commands, live reload of the Interface files, and the escape key. Modelled on herdr-app's `window.rs`.

use std::collections::HashMap;
use std::path::PathBuf;
use std::rc::Rc;
use std::time::Duration;

use gpui::{
    AnyElement, Context, Entity, ExternalPaths, FocusHandle, IntoElement, KeyBinding, KeyBindingContextPredicate, ParentElement, Render, SharedString,
    Styled, Window, div, prelude::*, px,
};
use gpui_shell::action::ShellAction;
use gpui_shell::policy::Policy;
use gpui_shell::{Capabilities, ShellRoot, ShellRuntime};
use serde_json::Value;

use crate::host::{self, Request};
use crate::layout::{self, Kind, Node, Size};
use crate::workspace;

pub const ESCAPE_HATCH: &str = "ctrl-alt-shift-escape";
/// The view drawn over the whole window while `shared("picker")` is set: pickers and prompts for every view.
const OVERLAY: &str = "overlay";
const TOASTS: &str = "toasts"; // timed notifications, bottom right, as the Console's
/// Settings, drawn over the whole window while `shared("settings")` is set (herdr-app's Settings window).
const SETTINGS: &str = "settings";
/// The exit code that asks the launcher to open the App again (Restart App), on a staged build if there is one.
pub const UPDATE_EXIT: i32 = 75;

/// A plain key nothing took, outside a text field, says so (element focus: no silent keys).
fn dead_key(ev: &gpui::KeystrokeEvent) {
    let k = &ev.keystroke;
    let plain = k.key_char.as_deref().is_some_and(|c| !c.trim().is_empty())
        && !k.modifiers.control && !k.modifiers.alt && !k.modifiers.platform;
    let typing = ev.context_stack.iter().any(|c| c.contains("Input"));
    if ev.action.is_none() && plain && !typing {
        host::toast(&format!("{} does nothing here · ? lists the keys", k.unparse()));
    }
}

/// One log line per key: the key (a letter typed into a field is "<text>"), the command its binding ran (none: typed
/// text or handled inside a view), the focused element's key contexts, the page, pane focus, shown Thread and
/// whether the window is active.
fn log_key(keystroke: &gpui::Keystroke, action: Option<String>, window: &Window) {
    let contexts: Vec<String> = window.context_stack().iter().map(|c| format!("{c:?}")).collect();
    let typing = contexts.iter().any(|c| c.starts_with("Input"));
    let text = keystroke.key_char.is_some() && !keystroke.modifiers.control && !keystroke.modifiers.alt && !keystroke.modifiers.platform;
    let key = if typing && text && action.is_none() { "<text>".to_owned() } else { keystroke.unparse() };
    crate::log::write("key", serde_json::json!({
        "key": key, "action": action, "contexts": contexts, "page": host::shared("page"), "focus": host::shared("focus"),
        "thread": host::shared("thread"), "active": window.is_window_active(),
    }));
}

/// Stages the Service's build in the background with the launcher's `--prepare` (`COMMS_TERMINAL_APP_PREPARE`), which
/// builds it into its own slot beside the running one. The App never quits for it: it stays open and read-only, and
/// the staged build opens at the next start (PRD 2026-11-04-app-update-on-restart, FR-UPD-1). Shared `update` names
/// the step for the views: building, ready or failed (unmanaged: no launcher, as in a dev run). One staging at a
/// time, once per Service build; `force` (Settings' Update now) tries the same build again.
fn prepare_update(service_build: &str, force: bool) {
    use std::sync::atomic::{AtomicBool, Ordering};
    static RUNNING: AtomicBool = AtomicBool::new(false);
    static TRIED: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());
    if RUNNING.load(Ordering::SeqCst) || (!force && *TRIED.lock().unwrap() == service_build) {
        return;
    }
    let Some(prepare) = std::env::var_os("COMMS_TERMINAL_APP_PREPARE") else {
        return set_update_step("unmanaged");
    };
    RUNNING.store(true, Ordering::SeqCst);
    *TRIED.lock().unwrap() = service_build.to_owned();
    set_update_step("building");
    std::thread::spawn(move || {
        let staged = std::process::Command::new(&prepare).arg("--prepare").status().is_ok_and(|s| s.success());
        RUNNING.store(false, Ordering::SeqCst);
        host::request(Request::UpdateStep(if staged { "ready" } else { "failed" }));
    });
}

/// On the window's thread: the update step every view reads (`shared("update")`), logged; null clears it.
fn set_update_step(step: &str) {
    let value = if step.is_empty() { Value::Null } else { Value::String(step.to_owned()) };
    if host::shared("update") != value {
        crate::log::write("update", serde_json::json!({"step": value}));
        host::set_shared("update", value);
        host::request(Request::Refresh);
    }
}

/// Restart App: the launcher opens the App again, on a staged build if there is one. A Composer's unsaved text
/// would be lost (a read-only core refuses its save), so the first press says so and the second restarts (FR-UPD-8).
fn restart() {
    static WARNED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    if !host::shared("draft_unsaved").is_null() && !WARNED.swap(true, std::sync::atomic::Ordering::SeqCst) {
        return toast("A Draft has unsaved text that a restart would lose. Copy it first; Restart App again restarts.", "warning");
    }
    std::process::exit(UPDATE_EXIT)
}

/// While an update keeps the core read-only, everything but the bars fades (FR-UPD-6); reading and moving still work.
fn faded() -> bool {
    !host::shared("update").is_null() && host::status()["online"] == Value::Bool(false)
}

struct Copy {
    root: Option<Entity<ShellRoot>>,
    error: Option<String>,
    commands: async_channel::Sender<String>,
}

pub struct AppWindow {
    home: PathBuf,
    runtime: Rc<ShellRuntime>,
    layout: Node,
    copies: HashMap<String, Copy>,
    focus: FocusHandle,
    problem: Option<String>,
    keys_installed: bool,
    /// The widgets' own bindings (a text field's backspace, enter, arrows), kept across keys.json reloads.
    base_keys: Option<Vec<KeyBinding>>,
    _key_log: gpui::Subscription,
    renders: u64,
}

fn capabilities(home: &std::path::Path) -> Capabilities {
    Capabilities::new().read_roots([home.to_path_buf()]).write_roots([home.join("data")]).storage(true).clipboard_write(true)
}

/// The policy the next load gets: its own `comms` module (its Origin and queues) and its own storage file.
/// gpui-shell copies the default policy into each load, so each copy needs a fresh default (herdr-app's rule).
pub fn prepare_policy(home: &std::path::Path, name: &str, origin: &'static str, commands: async_channel::Receiver<String>) {
    let data = home.join("data").join(name);
    let _ = std::fs::create_dir_all(&data);
    let policy = Policy::new()
        .with_application(format!("comms-terminal-app.{name}"))
        .with_capabilities(capabilities(home))
        .with_storage_path(data.join("store.json"))
        .with_host_module(host::module(origin, commands))
        .expect("the comms module is valid");
    gpui_shell::policy::set_default(policy);
}

impl AppWindow {
    pub fn new(
        home: PathBuf,
        requests: async_channel::Receiver<Request>,
        events: async_channel::Receiver<Value>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        let runtime = ShellRuntime::new_with_components(cx, crate::body::components()).expect("the script runtime starts");
        let focus = cx.focus_handle();
        window.focus(&focus, cx);
        let (layout, problem) = match load_layout(&home) {
            Ok(node) => (node, None),
            Err(e) => (shipped_layout(), Some(e)),
        };
        crate::log::open(&home);
        host::set_fonts(cx.text_system().all_font_names());
        crate::log::write("start", serde_json::json!({"version": env!("CARGO_PKG_VERSION"), "pid": std::process::id()}));
        // Every key goes in the log once: one that matched a binding here, with the command it ran; any other (typed
        // text, or a key a view handles itself, as Esc in the reply box) from the root's capture_key_down (render).
        let key_log = cx.observe_keystrokes(|_, ev, window, _| {
            dead_key(ev);
            if let Some(a) = &ev.action {
                let action = a.as_any().downcast_ref::<ShellAction>().map_or_else(|| a.name().to_string(), |s| s.id().to_string());
                log_key(&ev.keystroke, Some(action), window);
            }
        });
        let mut this = AppWindow {
            home, runtime, layout, copies: HashMap::new(), focus, problem, keys_installed: false, base_keys: None,
            _key_log: key_log, renders: 0,
        };
        this.install_keys(cx);
        this.load_views(window, cx);

        cx.spawn_in(window, async move |this, cx| {
            while let Ok(request) = requests.recv().await {
                let _ = this.update_in(cx, |this, window, cx| this.handle(request, window, cx));
            }
        })
        .detach();
        cx.spawn_in(window, async move |this, cx| {
            while let Ok(event) = events.recv().await {
                let status = event["kind"] == "status";
                if status {
                    let (was, now) = (host::status(), &event["data"]);
                    if was["online"] != now["online"] || was["problem"] != now["problem"] {
                        crate::log::write("status", serde_json::json!({"online": now["online"], "problem": now["problem"]}));
                    }
                    host::set_status(event["data"].clone());
                }
                if event["kind"] == "update" {
                    crate::log::write("update", event["data"].clone());
                }
                // The Service runs another build (AS-UPD-1): once no write is on its way, stage it while the App
                // stays open and read-only; it opens at the next start.
                if event["kind"] == "update" && host::status()["pending"].as_u64().unwrap_or(0) == 0 {
                    prepare_update(event["data"]["service_build"].as_str().unwrap_or(""), false);
                }
                // Online again on this build (the Service went back to it): no update to show.
                if status && host::status()["online"] == Value::Bool(true) {
                    set_update_step("");
                }
                host::broadcast(&event); // views that care render themselves
                if status {
                    let _ = this.update(cx, |_, cx| cx.notify());
                }
            }
        })
        .detach();
        this.watch_files(window, cx);
        // The core outlives the window: an update it reported while no window listened is staged now (AS-UPD-9).
        let status = host::status();
        if let (Some(service), Some(build)) = (status["service_build"].as_str(), status["build"].as_str()) {
            if !service.is_empty() && !build.ends_with(service) {
                prepare_update(service, false);
            }
        }
        this
    }

    fn load_views(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        for name in self.layout.views().into_iter().chain([OVERLAY.to_owned(), TOASTS.to_owned(), SETTINGS.to_owned()]) {
            if self.copies.contains_key(&name) {
                continue;
            }
            let copy = self.load(&name, window, cx);
            self.copies.insert(name, copy);
        }
    }

    fn load(&self, name: &str, window: &mut Window, cx: &mut Context<Self>) -> Copy {
        let (tx, rx) = async_channel::unbounded();
        let mut copy = Copy { root: None, error: None, commands: tx };
        let Some((dir, origin)) = workspace::locate(&self.home, name) else {
            copy.error = Some(format!("no view or Panel named {name:?} (views/{name} or panels/{name})"));
            return copy;
        };
        prepare_policy(&self.home, name, origin, rx);
        match self.runtime.try_load(&dir, window, cx) {
            Ok(root) => copy.root = Some(root),
            Err(e) => {
                crate::log::write("error", serde_json::json!({"view": name, "error": format!("{e:#}")}));
                copy.error = Some(format!("{e:#}"));
            }
        }
        copy
    }

    fn handle(&mut self, request: Request, window: &mut Window, cx: &mut Context<Self>) {
        match request {
            Request::Refresh => {
                for copy in self.copies.values() {
                    if let Some(root) = &copy.root {
                        let _ = self.runtime.refresh(root, cx);
                    }
                }
                cx.notify(); // the page, and so a block's view, may have changed
            }
            Request::Focus => window.focus(&self.focus, cx),
            Request::Run(command) => self.run(&command, window, cx),
            Request::Open(url) => crate::body::open(&url, cx),
            Request::Repaint => window.refresh(),
            Request::Control { cmd, reply } => self.control(cmd, reply, window, cx),
            Request::UpdateStep(step) => set_update_step(step),
        }
    }

    /// One control-socket request: shot, status, do, raise, probe.
    fn control(&mut self, cmd: Value, reply: std::sync::mpsc::Sender<Value>, window: &mut Window, cx: &mut Context<Self>) {
        crate::log::write("control", serde_json::json!({"verbs": cmd.as_object().map(|o| o.keys().cloned().collect::<Vec<_>>())}));
        if let Some(path) = cmd.get("shot").and_then(Value::as_str) {
            let path = PathBuf::from(path);
            let answer = match crate::control::save_shot(window, &path) {
                Ok(()) => serde_json::json!({"path": path}),
                Err(e) => serde_json::json!({"error": format!("screenshot failed: {e:#}")}),
            };
            let _ = reply.send(answer);
        } else if cmd.get("status").is_some() {
            let copies: serde_json::Map<String, Value> = self
                .copies
                .iter()
                .map(|(name, c)| (name.clone(), serde_json::json!({"loaded": c.root.is_some(), "error": c.error})))
                .collect();
            let m = self.runtime.read_metrics();
            let _ = reply.send(serde_json::json!({
                "core": host::status(), "update": host::shared("update"), "layout": self.layout.views(), "views": copies, "problem": self.problem,
                "home": self.home, "log": crate::log::path(&self.home),
                "metrics": {"window_renders": self.renders, "script_renders": m.script_renders(),
                            "materializations": m.materializations(), "frame_script_calls": m.frame_script_calls()},
            }));
        } else if let Some(command) = cmd.get("do").and_then(Value::as_str) {
            self.run(command, window, cx);
            let _ = reply.send(serde_json::json!({"ok": true}));
        } else if cmd.get("raise").is_some() {
            window.activate_window();
            let _ = reply.send(serde_json::json!({"ok": true}));
        } else if let Some(probe) = cmd.get("probe") {
            let moves = probe.get("moves").and_then(Value::as_u64).unwrap_or(200);
            let view = probe.get("view").and_then(Value::as_str).unwrap_or("done").to_owned();
            self.probe(moves, &view, reply, window, cx);
        } else {
            let _ = reply.send(serde_json::json!({"error": "unknown request"}));
        }
    }

    /// NFR-1 in the real App: `moves` Thread moves in one Status view (Done by default), 120 ms apart, each timed from the key
    /// command to the Thread view's first render of the new Thread (`mark("shown")`). Presenting that render
    /// adds at most one frame.
    fn probe(&mut self, moves: u64, view: &str, reply: std::sync::mpsc::Sender<Value>, window: &mut Window, cx: &mut Context<Self>) {
        self.run(&format!("nav.{view}"), window, cx);
        cx.spawn_in(window, async move |this, cx| {
            cx.background_executor().timer(Duration::from_secs(2)).await;
            *host::MARKS.lock().unwrap() = Some(Vec::new());
            for i in 0..moves {
                let command = if (i / 50) % 2 == 0 { "threads.next" } else { "threads.prev" }; // stays inside the list
                host::mark("move");
                let _ = this.update_in(cx, |this, window, cx| this.run(command, window, cx));
                cx.background_executor().timer(Duration::from_millis(120)).await;
            }
            cx.background_executor().timer(Duration::from_secs(1)).await;
            let marks = host::MARKS.lock().unwrap().take().unwrap_or_default();
            let mut times = Vec::new();
            for (i, (name, at)) in marks.iter().enumerate() {
                if name != "move" {
                    continue;
                }
                let shown = marks[i + 1..].iter().take_while(|(n, _)| n != "move").find(|(n, _)| n == "shown");
                if let Some((_, then)) = shown {
                    times.push(then.duration_since(*at).as_secs_f64() * 1000.0);
                }
            }
            times.sort_by(|a, b| a.total_cmp(b));
            let q = |p: f64| times.get(((times.len().max(1) - 1) as f64 * p).round() as usize).copied().unwrap_or(f64::NAN);
            let _ = reply.send(serde_json::json!({
                "moves": moves, "measured": times.len(), "p50_ms": q(0.5), "p95_ms": q(0.95), "max_ms": q(1.0),
            }));
        })
        .detach();
    }

    /// `app.reset`, or `<view>.<command>` to that view's `nextCommand()`.
    fn run(&mut self, command: &str, window: &mut Window, cx: &mut Context<Self>) {
        let view = command.split_once('.').map_or("", |(v, _)| v);
        let to = if view == "app" || self.copies.contains_key(view) { "sent" } else { "no such view" };
        crate::log::write("run", serde_json::json!({"command": command, "to": to}));
        if command == "app.reset" {
            return self.reset(window, cx);
        }
        if command == "app.attach" {
            return self.pick_files(cx);
        }
        // Settings' app actions (FR-SET-2..4): zoom writes settings.json, which the watcher applies at once.
        let zoom = |z: f32| { let _ = host::write_setting("zoom", serde_json::json!((z * 20.).round() / 20.)); };
        match command {
            "app.zoom_in" => return zoom((crate::theme::zoom() + 0.1).min(2.0)),
            "app.zoom_out" => return zoom((crate::theme::zoom() - 0.1).max(0.75)),
            "app.zoom_reset" => { let _ = host::write_setting("zoom", Value::Null); return; }
            "app.edit_keys" => return cx.open_with_system(&self.home.join("keys.json")),
            "app.update" => return prepare_update(host::status()["service_build"].as_str().unwrap_or(""), true),
            "app.restart" => return restart(),
            // The top bar's window controls and drag (FR-BAR-2): niri draws no title bar, so the App draws its own.
            "app.minimize" => return window.minimize_window(),
            "app.maximize" => return window.zoom_window(),
            "app.close" => return window.remove_window(),
            "app.move" => return window.start_window_move(),
            _ => {}
        }
        // scroll_end: far enough that the pane under the Thread column stops at its newest entries.
        if let Some(step) = match command {
            "app.scroll_down" => Some(1.),
            "app.scroll_up" => Some(-1.),
            "app.scroll_end" => Some(1000.),
            _ => None,
        } {
            return scroll_thread(step, window, cx);
        }
        if let Some((view, cmd)) = command.split_once('.') {
            if let Some(copy) = self.copies.get(view) {
                let _ = copy.commands.try_send(cmd.to_string());
            }
        }
    }

    /// The escape key: the shown layout and keys.json are set aside as `*.reset-<time>`, and the shipped ones
    /// come back. Nothing can rebind it.
    fn reset(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs());
        for path in ["layouts/main.json", "keys.json"] {
            let file = self.home.join(path);
            let _ = std::fs::rename(&file, file.with_extension(format!("json.reset-{stamp}")));
            if let Some(contents) = workspace::shipped(path) {
                let _ = std::fs::write(&file, contents);
            }
        }
        self.reload(window, cx);
    }

    fn reload(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        crate::theme::apply(&self.home, cx);
        match load_layout(&self.home) {
            Ok(node) => {
                self.layout = node;
                self.problem = None;
            }
            Err(e) => self.problem = Some(e),
        }
        self.install_keys(cx);
        self.load_views(window, cx);
        cx.notify();
    }

    fn install_keys(&mut self, cx: &mut Context<Self>) {
        let keys = match workspace::read_json(&self.home, "keys.json") {
            Ok(keys) => Ok(keys),
            Err(e) if self.keys_installed => {
                self.problem = Some(e); // keep the last good bindings (keys.error says why)
                return;
            }
            Err(e) => {
                self.problem = Some(e);
                serde_json::from_str(workspace::shipped("keys.json").unwrap_or("{}")).map_err(|e| e.to_string())
            }
        };
        let mut bindings = vec![KeyBinding::new(ESCAPE_HATCH, ShellAction::new("app.reset"), None)];
        if let Ok(Value::Object(sections)) = keys {
            // Later bindings win at the same depth: the "detail" and "rail" sections come after the pages, so j/k in
            // the detail column reach its view (element focus) while the page's letter keys still apply there.
            let mut sections: Vec<(String, Value)> = sections.into_iter().collect();
            sections.sort_by_key(|(s, _)| matches!(s.as_str(), "detail" | "rail"));
            for (section, map) in sections {
                // Plain keys stay quiet while a text field has the keyboard; `always` keys (ctrl-enter, escape) do not.
                let context = match section.as_str() {
                    "always" => None,
                    "global" => Some("!Input".to_owned()),
                    // The rail's own keys while it has the focus; the page's keys wait until focus leaves it.
                    "rail" => Some("rail && !Input".to_owned()),
                    "detail" => Some("detail && !Input".to_owned()),
                    s => s
                        .strip_prefix("page:")
                        .map(|p| format!("page == {p} && !Input && !rail"))
                        .or_else(|| s.strip_prefix("view:").map(|v| format!("view == {v}"))),
                };
                if section != "always" && context.is_none() {
                    continue;
                }
                for (keystroke, action) in map.as_object().into_iter().flatten() {
                    if keystroke == ESCAPE_HATCH {
                        continue; // reserved
                    }
                    let Some(action) = action.as_str() else { continue };
                    let predicate = match context.as_deref().map(KeyBindingContextPredicate::parse) {
                        None => None,
                        Some(Ok(p)) => Some(Rc::new(p)),
                        Some(Err(e)) => {
                            self.problem = Some(format!("keys.json: {section}: {e}"));
                            continue;
                        }
                    };
                    let action = Box::new(ShellAction::new(action.to_string()));
                    match KeyBinding::load(keystroke, action, predicate, false, None, &gpui::DummyKeyboardMapper) {
                        Ok(binding) => bindings.push(binding),
                        Err(e) => self.problem = Some(format!("keys.json: {keystroke:?}: {e}")),
                    }
                }
            }
        }
        // Clearing drops the widgets' bindings too (text fields lost backspace and enter), so put them back first.
        let base = self.base_keys.get_or_insert_with(|| cx.key_bindings().borrow().bindings().cloned().collect()).clone();
        cx.clear_key_bindings();
        cx.bind_keys(base);
        cx.bind_keys(bindings);
        self.keys_installed = true;
    }

    /// One OS file watcher for the whole home (herdr-app's way, not gpui-shell's per-view polling, which kept
    /// the idle window busy): layouts, keys, settings and themes reload the window; a view's or Panel's folder
    /// reloads that copy only, keeping its last good version when the new one fails.
    fn watch_files(&self, window: &mut Window, cx: &mut Context<Self>) {
        use notify::Watcher as _;
        let (tx, rx) = async_channel::unbounded::<PathBuf>();
        let watched = self.home.clone();
        let watcher = notify::recommended_watcher(move |event: notify::Result<notify::Event>| {
            let Ok(event) = event else { return };
            // Reading a file (as a reload does) is an Access event; only writes, creates, renames and removals
            // are edits, or each reload would set off the next.
            use notify::event::{EventKind, ModifyKind};
            if matches!(event.kind, EventKind::Access(_) | EventKind::Modify(ModifyKind::Metadata(_)) | EventKind::Other) {
                return;
            }
            for path in event.paths {
                let Ok(rel) = path.strip_prefix(&watched) else { continue };
                let name = rel.file_name().and_then(|n| n.to_str()).unwrap_or("");
                // Files the App or the runtime write itself, and editor leftovers, are not edits.
                if name.starts_with('.') || name.ends_with('~') || name.ends_with(".error") || name == "gpui-kit.d.ts"
                    || name == "jsconfig.json" || rel.starts_with("data") || rel.components().any(|c| c.as_os_str() == "node_modules")
                {
                    continue;
                }
                let _ = tx.try_send(rel.to_path_buf());
            }
        });
        let Ok(mut watcher) = watcher else { return };
        if watcher.watch(&self.home, notify::RecursiveMode::Recursive).is_err() {
            return;
        }
        cx.spawn_in(window, async move |this, cx| {
            let _watcher = watcher;
            while let Ok(first) = rx.recv().await {
                cx.background_executor().timer(Duration::from_millis(80)).await;
                let mut changed = vec![first];
                while let Ok(more) = rx.try_recv() {
                    changed.push(more);
                }
                let _ = this.update_in(cx, |this, window, cx| this.files_changed(changed, window, cx));
            }
        })
        .detach();
    }

    fn files_changed(&mut self, changed: Vec<PathBuf>, window: &mut Window, cx: &mut Context<Self>) {
        let mut config = false;
        let mut copies = Vec::new();
        for rel in changed {
            let mut parts = rel.components().map(|c| c.as_os_str().to_string_lossy().into_owned());
            match parts.next().as_deref() {
                Some("views" | "panels") => {
                    if let Some(name) = parts.next() {
                        if !copies.contains(&name) {
                            copies.push(name);
                        }
                    }
                }
                Some("layouts" | "themes" | "keys.json" | "settings.json") => config = true,
                _ => {}
            }
        }
        for name in copies {
            let Some(copy) = self.copies.get_mut(&name) else { continue };
            match &copy.root {
                Some(root) => {
                    copy.error = self.runtime.reload_now(root, window, cx).err().map(|e| format!("{e:#}"));
                }
                None => {
                    self.copies.remove(&name); // never loaded: try again from scratch
                }
            }
        }
        if config {
            self.reload(window, cx);
        } else {
            self.load_views(window, cx);
            cx.notify();
        }
    }

    /// One node. `row` says which way its parent lays out, so a fixed size is a width in a row and a height in
    /// a column.
    fn render_node(&self, node: &Node, row: bool) -> AnyElement {
        use crate::theme::color;
        if node.kind == Kind::Block {
            let (page, detail) = (host::shared("page"), host::shared("detail"));
            if node.view_on(page.as_str(), detail.as_str()) == Some("") {
                return div().into_any_element(); // hidden on this page: a full page takes the room
            }
        }
        let mut el = div().relative().min_w_0().min_h_0();
        el = match node.size {
            Size::Px(n) if row => el.flex_none().w(px(n * crate::theme::zoom())).h_full(),
            Size::Px(n) => el.flex_none().h(px(n * crate::theme::zoom())).w_full(),
            Size::Fill => el.flex_1(),
        };
        if let Some(token) = &node.background {
            el = el.bg(color(token));
        }
        if let Some((side, token)) = &node.border {
            el = match side.as_str() {
                "left" => el.border_l_1(),
                "right" => el.border_r_1(),
                "top" => el.border_t_1(),
                "bottom" => el.border_b_1(),
                _ => el.border_1(),
            }
            .border_color(color(token));
        }
        if node.card {
            // The focused pane's border turns blue, as the Console's does (← → move the focus).
            // Elements have the focus, not panes: the focused element and its path carry accent bars in their views.
            el = el.rounded(px(10.)).border_1().border_color(color("border")).bg(color("background")).overflow_hidden();
        }
        if node.padding > 0. {
            el = el.p(px(node.padding));
        }
        if node.gap > 0. {
            el = el.gap(px(node.gap));
        }
        match node.kind {
            Kind::Row => el.flex().flex_row().children(node.children.iter().map(|c| self.render_node(c, true))).into_any_element(),
            Kind::Column => el.flex().flex_col().children(node.children.iter().map(|c| self.render_node(c, false))).into_any_element(),
            Kind::Block => {
                let (page, detail) = (host::shared("page"), host::shared("detail"));
                let name = node.view_on(page.as_str(), detail.as_str()).unwrap_or_default().to_owned();
                let mut el = el.overflow_hidden().key_context(format!("View view={name}").as_str());
                if faded() && name != "status" && name != "topbar" {
                    el = el.opacity(0.45); // the bars stay clear: the bottom bar says what the update is doing
                }
                match self.copies.get(&name) {
                    Some(Copy { root: Some(root), .. }) => el.child(root.clone()).into_any_element(),
                    Some(Copy { error: Some(e), .. }) => el
                        .p_2()
                        .text_color(color("danger"))
                        .text_size(px(12.))
                        .child(SharedString::from(format!("{} ({name}): {e}", node.id)))
                        .into_any_element(),
                    _ => el.into_any_element(),
                }
            }
        }
    }
}

impl Render for AppWindow {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        use crate::theme::color;
        self.renders += 1;
        // Views fit their content to it: the Thread page hides its properties column when narrow, as the Console's.
        // In the units views write (zoom 1), so a zoomed App fits its narrow rules the same way.
        let width = serde_json::json!((f32::from(window.viewport_size().width) / crate::theme::zoom()).round());
        if host::shared("width") != width {
            host::set_shared("width", width);
            host::request(host::Request::Refresh); // views re-render with the new width
        }
        let page = host::shared("page");
        let page = page.as_str().unwrap_or("threads");
        let rail = match host::shared("focus").as_str() { Some("rail") => " rail", Some("detail") => " detail", _ => "" };
        // The view in each column on this page, for nav (← →, j/k in the detail column); "" when hidden.
        let (page_now, detail_now) = (host::shared("page"), host::shared("detail"));
        for block in ["list", "detail"] {
            let view = self.layout.find(block).and_then(|n| n.view_on(page_now.as_str(), detail_now.as_str())).unwrap_or("");
            if host::shared(&format!("{block}_view")).as_str() != Some(view) {
                host::set_shared(&format!("{block}_view"), Value::String(view.to_owned()));
            }
        }
        let mut root = div()
            .key_context(format!("Page page={page}{rail}").as_str())
            .relative()
            .size_full()
            .flex()
            .flex_col()
            .bg(color("canvas"))
            .text_color(color("foreground"))
            .text_size(px(13. * crate::theme::zoom()))
            .track_focus(&self.focus)
            .capture_key_down(|ev, window, _| log_key(&ev.keystroke, None, window))
            .on_action(cx.listener(|this, action: &ShellAction, window, cx| this.run(action.id(), window, cx)))
            .on_drop(cx.listener(|this, paths: &ExternalPaths, _, cx| this.attach(paths, cx)))
            .child(div().flex_1().min_h_0().flex().child(self.render_node(&self.layout, true)));
        if host::shared("toasts").as_u64().unwrap_or(0) > 0 {
            if let Some(Copy { root: Some(toasts), .. }) = self.copies.get(TOASTS) {
                root = root.child(div().absolute().right(px(12.)).bottom(px(36.)).w(px(440.)).h(px(320.)).child(toasts.clone()));
            }
        }
        if !host::shared("settings").is_null() {
            if let Some(Copy { root: Some(settings), .. }) = self.copies.get(SETTINGS) {
                root = root.child(div().absolute().inset_0().child(settings.clone()));
            }
        }
        if !host::shared("picker").is_null() {
            if let Some(Copy { root: Some(overlay), .. }) = self.copies.get(OVERLAY) {
                root = root.child(div().absolute().inset_0().child(overlay.clone()));
            }
        }
        if let Some(font) = crate::theme::font() {
            root = root.font_family(SharedString::from(font));
        }
        if let Some(problem) = &self.problem {
            root = root.child(
                div().px_3().py_1().bg(color("danger")).text_color(color("background")).text_size(px(12.))
                    .child(SharedString::from(format!("{problem} · ctrl-alt-shift-escape restores the defaults"))),
            );
        }
        root
    }
}

impl AppWindow {
    /// Files dropped on the window go on the Draft shown (Drafts, or a Draft on Today), else on the selected
    /// Thread's reply (FR-10): the core stages each through the Service's upload page, and the view shows them
    /// when its rows change.
    fn attach(&mut self, paths: &ExternalPaths, cx: &mut Context<Self>) {
        self.stage(paths.paths().to_vec(), cx);
    }

    /// ctrl+o (D63): the system file picker; the chosen files are staged as dropped ones are.
    fn pick_files(&mut self, cx: &mut Context<Self>) {
        let picked = cx.prompt_for_paths(gpui::PathPromptOptions { files: true, directories: false, multiple: true, prompt: None });
        cx.spawn(async move |this, cx| {
            if let Ok(Ok(Some(paths))) = picked.await {
                let _ = this.update(cx, |this, cx| this.stage(paths, cx));
            }
        })
        .detach();
    }

    fn stage(&mut self, paths: Vec<PathBuf>, cx: &mut Context<Self>) {
        let on_draft = host::shared("page") == "drafts" || (host::shared("page") == "today" && host::shared("detail") == "draft");
        let (kind, owner) = if on_draft { ("draft", host::shared("draft")) } else { ("thread", host::shared("thread")) };
        let (Some(owner), Some(core)) = (owner.as_str().map(str::to_owned), host::core()) else {
            return toast("Select a Thread or Draft first: dropped files go on its reply.", "warning");
        };
        let paths: Vec<PathBuf> = paths.into_iter().filter(|p| p.is_file()).collect();
        cx.background_spawn(async move {
            for path in paths {
                let params = serde_json::json!({"origin": "view", "owner_kind": kind, "owner_id": owner, "path": path});
                if let Err(e) = core.clone().call("upload".into(), params).await {
                    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                    toast(&format!("{name} was not attached: {e}"), "error");
                }
            }
        })
        .detach();
    }
}

/// space / pgdn / pgup (D55): a page of the Thread's messages, as the Console's scroll_thread. GPUI keeps a plain
/// scroll area's offset to itself, so this sends the wheel event a trackpad would, over the messages column.
// ponytail: the point assumes the shipped layout (Thread pane on the right, properties column 300 px); find the
// pane's bounds if Interface files move it.
fn scroll_thread(step: f32, window: &mut Window, cx: &mut Context<AppWindow>) {
    let size = window.viewport_size();
    let event = gpui::ScrollWheelEvent {
        position: gpui::point(size.width - px(480.), size.height * 0.5),
        delta: gpui::ScrollDelta::Pixels(gpui::point(px(0.), -size.height * 0.8 * step)),
        modifiers: gpui::Modifiers::default(),
        touch_phase: gpui::TouchPhase::Moved,
    };
    window.dispatch_event(gpui::PlatformInput::ScrollWheel(event), cx);
}

/// A toast in the views, as the core's own toasts arrive.
fn toast(message: &str, severity: &str) {
    host::broadcast(&serde_json::json!({"kind": "toast", "data": {"message": message, "severity": severity}}));
}

fn load_layout(home: &std::path::Path) -> Result<Node, String> {
    workspace::read_json(home, "layouts/main.json").and_then(|v| layout::parse(&v))
}

fn shipped_layout() -> Node {
    let v: Value = serde_json::from_str(workspace::shipped("layouts/main.json").unwrap()).unwrap();
    layout::parse(&v).unwrap()
}

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
/// The exit code that asks the launcher to install the Service's build (tui.UPDATE_EXIT).
pub const UPDATE_EXIT: i32 = 75;

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
        let mut this = AppWindow { home, runtime, layout, copies: HashMap::new(), focus, problem, keys_installed: false, renders: 0 };
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
                    host::set_status(event["data"].clone());
                }
                // The Service runs another build (AS-9): once no write is on its way, quit with 75 so
                // deploy/comms-terminal-app installs that build and opens the App again, as the Console does.
                if event["kind"] == "update" && host::status()["pending"].as_u64().unwrap_or(0) == 0 {
                    std::process::exit(UPDATE_EXIT);
                }
                host::broadcast(&event); // views that care render themselves
                if status {
                    let _ = this.update(cx, |_, cx| cx.notify());
                }
            }
        })
        .detach();
        this.watch_files(window, cx);
        this
    }

    fn load_views(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        for name in self.layout.views().into_iter().chain([OVERLAY.to_owned(), TOASTS.to_owned()]) {
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
            Err(e) => copy.error = Some(format!("{e:#}")),
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
        }
    }

    /// One control-socket request: shot, status, do, raise, probe.
    fn control(&mut self, cmd: Value, reply: std::sync::mpsc::Sender<Value>, window: &mut Window, cx: &mut Context<Self>) {
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
                "core": host::status(), "layout": self.layout.views(), "views": copies, "problem": self.problem,
                "home": self.home,
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
        if command == "app.reset" {
            return self.reset(window, cx);
        }
        if command == "app.attach" {
            return self.pick_files(cx);
        }
        if let Some(step) = match command { "app.scroll_down" => Some(1.), "app.scroll_up" => Some(-1.), _ => None } {
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
            for (section, map) in sections {
                // Plain keys stay quiet while a text field has the keyboard; `always` keys (ctrl-enter, escape) do not.
                let context = match section.as_str() {
                    "always" => None,
                    "global" => Some("!Input".to_owned()),
                    // The rail's own keys while it has the focus; the page's keys wait until focus leaves it.
                    "rail" => Some("rail && !Input".to_owned()),
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
        cx.clear_key_bindings();
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
            Size::Px(n) if row => el.flex_none().w(px(n)).h_full(),
            Size::Px(n) => el.flex_none().h(px(n)).w_full(),
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
            let focused = host::shared("focus").as_str() == Some(node.id.as_str());
            el = el.rounded(px(10.)).border_1().border_color(color(if focused { "accent" } else { "border" })).bg(color("background")).overflow_hidden();
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
                let el = el.overflow_hidden().key_context(format!("View view={name}").as_str());
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
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        use crate::theme::color;
        self.renders += 1;
        let page = host::shared("page");
        let page = page.as_str().unwrap_or("threads");
        let rail = if host::shared("focus").as_str() == Some("rail") { " rail" } else { "" };
        let mut root = div()
            .key_context(format!("Page page={page}{rail}").as_str())
            .relative()
            .size_full()
            .flex()
            .flex_col()
            .bg(color("canvas"))
            .text_color(color("foreground"))
            .text_size(px(13.))
            .track_focus(&self.focus)
            .on_action(cx.listener(|this, action: &ShellAction, window, cx| this.run(action.id(), window, cx)))
            .on_drop(cx.listener(|this, paths: &ExternalPaths, _, cx| this.attach(paths, cx)))
            .child(div().flex_1().min_h_0().flex().child(self.render_node(&self.layout, true)));
        if host::shared("toasts").as_u64().unwrap_or(0) > 0 {
            if let Some(Copy { root: Some(toasts), .. }) = self.copies.get(TOASTS) {
                root = root.child(div().absolute().right(px(12.)).bottom(px(36.)).w(px(440.)).h(px(320.)).child(toasts.clone()));
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

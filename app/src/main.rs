//! comms-terminal-app: Comms Terminal's native desktop App (ADR 0013). A GPUI host for gpui-shell views, on the
//! Local core. `comms-terminal-app` opens the window; `comms-terminal-app check <dir>` checks a view or Panel.

mod body;
mod control;
mod host;
mod layout;
mod log;
mod rpc;
mod theme;
mod window;
mod workspace;

use gpui::{AppContext, Bounds, TitlebarOptions, WindowBounds, WindowOptions, px, size};
use serde_json::{Value, json};

const HELP: &str = "comms-terminal-app: Comms Terminal's desktop App (ADR 0013)

  comms-terminal-app               open the window (starts the Local core when it is not running)
  comms-terminal-app check <dir>   load a view or Panel, render it once, exit nonzero on error
  comms-terminal-app types <dir>   write gpui-kit.d.ts for a view or Panel
  comms-terminal-app shot [f.png]  save the running window's last frame as a PNG
  comms-terminal-app status        the running window: views loaded, errors, the Local core's status
  comms-terminal-app do <command>  run a command in the running window (threads.next, app.reset, …)
  comms-terminal-app probe [n]     time n Thread moves in the running window (NFR-1)

Interface files: ~/.config/comms-terminal-app (AGENTS.md explains them).";

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("check" | "types") => {
            let home = workspace::home();
            let _ = workspace::write(&home);
            let (_tx, rx) = async_channel::unbounded();
            window::prepare_policy(&home, "check", "panel", rx); // so `import … from "comms"` resolves
            gpui_shell::host::main_with_components(body::components());
        }
        Some("shot" | "status" | "do" | "probe") => std::process::exit(control::client(&args[1..])),
        Some("--help" | "-h" | "help") => println!("{HELP}"),
        Some(other) => {
            eprintln!("comms-terminal-app: unknown command {other:?}\n\n{HELP}");
            std::process::exit(2);
        }
        None => run(),
    }
}

fn run() {
    if control::raise_running() {
        return; // one App per home: the running one came forward
    }
    let home = workspace::home();
    if let Err(e) = workspace::write(&home) {
        eprintln!("comms-terminal-app: cannot write {}: {e}", home.display());
        std::process::exit(1);
    }
    let settings = workspace::read_json(&home, "settings.json").unwrap_or(Value::Null);
    // The command that starts the core: COMMS_TERMINAL_CORE (a JSON list, set by deploy/comms-terminal-app), else
    // settings.json's "core", else `comms-terminal` on PATH.
    let from_env = std::env::var("COMMS_TERMINAL_CORE").ok().and_then(|s| serde_json::from_str::<Value>(&s).ok());
    let command: Vec<String> = from_env
        .as_ref()
        .or_else(|| settings.get("core"))
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|s| s.as_str().map(str::to_owned)).collect())
        .filter(|c: &Vec<String>| !c.is_empty())
        .unwrap_or_else(|| vec!["comms-terminal".into()]);
    let (events_tx, events_rx) = async_channel::unbounded();
    let core = rpc::Core::connect(&rpc::socket_path(), &command, events_tx).unwrap_or_else(|e| {
        eprintln!("comms-terminal-app: {e}");
        std::process::exit(1);
    });
    match core.call_blocking("events.subscribe", json!({"origin": "view"})) {
        Ok(answer) => host::set_status(answer["status"].clone()),
        Err(e) => {
            eprintln!("comms-terminal-app: the Local core refused events.subscribe: {e}");
            std::process::exit(1);
        }
    }
    let (requests_tx, requests_rx) = async_channel::unbounded();
    control::listen(requests_tx.clone());
    host::install(core, requests_tx);

    gpui_platform::application().with_assets(gpui_shell::AppAssets::new(home.clone())).run(move |cx| {
        gpui_shell::init(cx);
        theme::apply(&home, cx);
        let bounds = Bounds::centered(None, size(px(1280.), px(800.)), cx);
        let options = WindowOptions {
            window_bounds: Some(WindowBounds::Windowed(bounds)),
            titlebar: Some(TitlebarOptions { title: Some("Comms Terminal".into()), ..Default::default() }),
            app_id: Some("comms-terminal-app".into()),
            ..Default::default()
        };
        cx.open_window(options, |window, cx| {
            cx.new(|cx| window::AppWindow::new(home, requests_rx, events_rx, window, cx))
        })
        .expect("the window opens");
        cx.activate(true);
    });
}

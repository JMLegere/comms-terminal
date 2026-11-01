//! The Interface files (ADR 0013) in `~/.config/comms-terminal-app`, as herdr-app keeps its own: shipped views,
//! `AGENTS.md` and the type file are rewritten on every start; layouts, keys, settings and themes are written
//! only when missing, so what Jeremy or an agent changed stays. Panels are theirs alone.

use std::path::{Path, PathBuf};

use serde_json::Value;

/// (path under the App's home, contents, rewritten on every start)
const DEFAULTS: &[(&str, &str, bool)] = &[
    ("AGENTS.md", include_str!("../defaults/AGENTS.md"), true),
    ("comms.d.ts", include_str!("../defaults/comms.d.ts"), true),
    ("views/brand/main.js", include_str!("../defaults/views/brand/main.js"), true),
    ("views/nav/main.js", include_str!("../defaults/views/nav/main.js"), true),
    ("views/topbar/main.js", include_str!("../defaults/views/topbar/main.js"), true),
    ("icons/LICENSE-Lucide.txt", include_str!("../defaults/icons/LICENSE-Lucide.txt"), true),
    ("icons/archive.svg", include_str!("../defaults/icons/archive.svg"), true),
    ("icons/chevron-down.svg", include_str!("../defaults/icons/chevron-down.svg"), true),
    ("icons/chevron-right.svg", include_str!("../defaults/icons/chevron-right.svg"), true),
    ("icons/circle-check.svg", include_str!("../defaults/icons/circle-check.svg"), true),
    ("icons/clock.svg", include_str!("../defaults/icons/clock.svg"), true),
    ("icons/file-text.svg", include_str!("../defaults/icons/file-text.svg"), true),
    ("icons/folder.svg", include_str!("../defaults/icons/folder.svg"), true),
    ("icons/git-branch.svg", include_str!("../defaults/icons/git-branch.svg"), true),
    ("icons/layers.svg", include_str!("../defaults/icons/layers.svg"), true),
    ("icons/monitor.svg", include_str!("../defaults/icons/monitor.svg"), true),
    ("icons/plus.svg", include_str!("../defaults/icons/plus.svg"), true),
    ("icons/search.svg", include_str!("../defaults/icons/search.svg"), true),
    ("icons/server.svg", include_str!("../defaults/icons/server.svg"), true),
    ("icons/settings.svg", include_str!("../defaults/icons/settings.svg"), true),
    ("icons/square-pen.svg", include_str!("../defaults/icons/square-pen.svg"), true),
    ("icons/square.svg", include_str!("../defaults/icons/square.svg"), true),
    ("icons/x.svg", include_str!("../defaults/icons/x.svg"), true),
    ("views/threads/main.js", include_str!("../defaults/views/threads/main.js"), true),
    ("views/thread/main.js", include_str!("../defaults/views/thread/main.js"), true),
    ("views/status/main.js", include_str!("../defaults/views/status/main.js"), true),
    ("layouts/main.json", include_str!("../defaults/layouts/main.json"), false),
    ("keys.json", include_str!("../defaults/keys.json"), false),
    ("settings.json", include_str!("../defaults/settings.json"), false),
    ("themes/gruvbox.json", include_str!("../defaults/themes/gruvbox.json"), false),
    ("themes/gruvbox-material-light.json", include_str!("../defaults/themes/gruvbox-material-light.json"), false),
];

pub fn home() -> PathBuf {
    if let Some(dir) = std::env::var_os("COMMS_TERMINAL_APP_HOME") {
        return PathBuf::from(dir);
    }
    let config = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config"));
    config.join("comms-terminal-app")
}

/// Writes the shipped files. A file whose contents already match is left alone, so an editor watching the folder
/// is not woken on every start.
pub fn write(home: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(home.join("panels"))?;
    for (path, contents, always) in DEFAULTS {
        let file = home.join(path);
        if file.exists() && (!always || std::fs::read_to_string(&file).is_ok_and(|now| now == *contents)) {
            continue;
        }
        if let Some(parent) = file.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&file, contents)?;
    }
    Ok(())
}

/// The shipped default of an Interface file, for restoring it (the escape key) or falling back to it.
pub fn shipped(path: &str) -> Option<&'static str> {
    DEFAULTS.iter().find(|(p, _, _)| *p == path).map(|(_, contents, _)| *contents)
}

/// A JSON Interface file: its value, or why it is unusable. A good read removes `<name>.error`; a bad one
/// writes it and the caller keeps the last good value (herdr-app's rule).
pub fn read_json(home: &Path, path: &str) -> Result<Value, String> {
    let file = home.join(path);
    let error_file = file.with_extension("error");
    let result = std::fs::read_to_string(&file)
        .map_err(|e| format!("{path}: {e}"))
        .and_then(|text| serde_json::from_str::<Value>(&text).map_err(|e| format!("{path}: {e}")));
    match &result {
        Ok(_) => {
            let _ = std::fs::remove_file(&error_file);
        }
        Err(message) => {
            let _ = std::fs::write(&error_file, format!("{message}\nThe App keeps the last good version.\n"));
        }
    }
    result
}

/// Where a view lives and the Origin it runs with: a shipped view, or a Panel.
pub fn locate(home: &Path, name: &str) -> Option<(PathBuf, &'static str)> {
    let view = home.join("views").join(name);
    if view.join("main.js").is_file() {
        return Some((view, "view"));
    }
    let panel = home.join("panels").join(name);
    panel.join("main.js").is_file().then_some((panel, "panel"))
}

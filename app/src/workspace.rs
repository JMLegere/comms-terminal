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
    ("icons/building-2.svg", include_str!("../defaults/icons/building-2.svg"), true),
    ("icons/calendar.svg", include_str!("../defaults/icons/calendar.svg"), true),
    ("icons/hash.svg", include_str!("../defaults/icons/hash.svg"), true),
    ("icons/list-checks.svg", include_str!("../defaults/icons/list-checks.svg"), true),
    ("icons/sun.svg", include_str!("../defaults/icons/sun.svg"), true),
    ("icons/trending-up.svg", include_str!("../defaults/icons/trending-up.svg"), true),
    ("icons/users.svg", include_str!("../defaults/icons/users.svg"), true),
    ("icons/workflow.svg", include_str!("../defaults/icons/workflow.svg"), true),
    ("views/calendar/main.js", include_str!("../defaults/views/calendar/main.js"), true),
    ("views/case/main.js", include_str!("../defaults/views/case/main.js"), true),
    ("views/cases/main.js", include_str!("../defaults/views/cases/main.js"), true),
    ("views/channels/main.js", include_str!("../defaults/views/channels/main.js"), true),
    ("views/connection/main.js", include_str!("../defaults/views/connection/main.js"), true),
    ("views/draft/main.js", include_str!("../defaults/views/draft/main.js"), true),
    ("views/side/main.js", include_str!("../defaults/views/side/main.js"), true),
    ("views/day/main.js", include_str!("../defaults/views/day/main.js"), true),
    ("views/drafts/main.js", include_str!("../defaults/views/drafts/main.js"), true),
    ("views/event/main.js", include_str!("../defaults/views/event/main.js"), true),
    ("views/organization/main.js", include_str!("../defaults/views/organization/main.js"), true),
    ("views/organizations/main.js", include_str!("../defaults/views/organizations/main.js"), true),
    ("views/overlay/main.js", include_str!("../defaults/views/overlay/main.js"), true),
    ("views/people/main.js", include_str!("../defaults/views/people/main.js"), true),
    ("views/person/main.js", include_str!("../defaults/views/person/main.js"), true),
    ("views/pipeline/main.js", include_str!("../defaults/views/pipeline/main.js"), true),
    ("views/sequence/main.js", include_str!("../defaults/views/sequence/main.js"), true),
    ("views/sequences/main.js", include_str!("../defaults/views/sequences/main.js"), true),
    ("views/task/main.js", include_str!("../defaults/views/task/main.js"), true),
    ("views/tasks/main.js", include_str!("../defaults/views/tasks/main.js"), true),
    ("views/today/main.js", include_str!("../defaults/views/today/main.js"), true),
    ("icons/mail.svg", include_str!("../defaults/icons/mail.svg"), true),
    ("icons/phone.svg", include_str!("../defaults/icons/phone.svg"), true),
    ("icons/message-square.svg", include_str!("../defaults/icons/message-square.svg"), true),
    ("icons/circle-x.svg", include_str!("../defaults/icons/circle-x.svg"), true),
    ("views/toasts/main.js", include_str!("../defaults/views/toasts/main.js"), true),
    ("layouts/main.json", include_str!("../defaults/layouts/main.json"), false),
    ("keys.json", include_str!("../defaults/keys.json"), false),
    ("settings.json", include_str!("../defaults/settings.json"), false),
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
///
/// A file Jeremy may edit (layout, keys, settings, theme) is written when missing, or replaced when it still equals
/// an earlier shipped version (`PREVIOUS`). An edited one is kept, and the new shipped version lands beside it as
/// `<file>.shipped` to compare and copy from.
pub fn write(home: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(home.join("panels"))?;
    for (path, contents, always) in DEFAULTS {
        let mut file = home.join(path);
        let now = std::fs::read(&file).ok();
        if now.as_deref() == Some(contents.as_bytes()) {
            continue;
        }
        if let (Some(now), false) = (&now, *always) {
            if !PREVIOUS.contains(&(*path, fnv(now))) {
                file = home.join(format!("{path}.shipped"));
                if std::fs::read(&file).ok().as_deref() == Some(contents.as_bytes()) {
                    continue;
                }
            }
        }
        if let Some(parent) = file.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&file, contents)?;
    }
    Ok(())
}

/// FNV-1a hashes of every earlier shipped version of the files Jeremy may edit. Add the old hash here whenever one of
/// those shipped files changes, so installed Apps take the new version.
const PREVIOUS: &[(&str, u64)] = &[
    ("layouts/main.json", 0xb363995d330f6414),
    ("layouts/main.json", 0x4e2034e653395857),
    ("keys.json", 0x1f4eb1ede7a5640e),
    ("keys.json", 0xb41b1cc21ba85367),
    ("keys.json", 0x425cfe7966802582),
    ("keys.json", 0x4cf57e992252d03b),
    ("keys.json", 0xe0ffd191c9049f42),
    ("keys.json", 0x2f488345fba78d9d),
];

fn fnv(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0xcbf29ce484222325, |h, b| (h ^ *b as u64).wrapping_mul(0x100000001b3))
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

#[cfg(test)]
mod tests {
    use super::*;

    /// AS-7: a broken keys.json writes keys.error and the caller keeps its last good keys; a good one clears it.
    #[test]
    fn a_broken_file_says_why_and_a_good_one_clears_it() {
        let home = std::env::temp_dir().join(format!("ct-app-read-json-{}", std::process::id()));
        std::fs::create_dir_all(&home).unwrap();
        std::fs::write(home.join("keys.json"), "{ \"global\": ").unwrap();
        assert!(read_json(&home, "keys.json").is_err());
        let why = std::fs::read_to_string(home.join("keys.error")).unwrap();
        assert!(why.starts_with("keys.json: ") && why.contains("keeps the last good version"), "{why}");
        std::fs::write(home.join("keys.json"), shipped("keys.json").unwrap()).unwrap();
        assert!(read_json(&home, "keys.json").unwrap()["global"].is_object());
        assert!(!home.join("keys.error").exists());
        std::fs::remove_dir_all(&home).unwrap();
    }

    /// An earlier shipped keys.json is replaced; an edited one is kept with the new version beside it.
    #[test]
    fn an_old_shipped_file_updates_and_an_edited_one_stays() {
        let home = std::env::temp_dir().join(format!("ct-app-write-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(&home).unwrap();
        let old = std::process::Command::new("git").args(["show", "05a23eb:app/defaults/keys.json"]).output().map(|o| o.stdout).unwrap_or_default();
        if !old.is_empty() {
            std::fs::write(home.join("keys.json"), &old).unwrap();
            write(&home).unwrap();
            assert_eq!(std::fs::read_to_string(home.join("keys.json")).unwrap(), shipped("keys.json").unwrap());
        }
        std::fs::write(home.join("keys.json"), "{\"global\": {}}").unwrap();
        write(&home).unwrap();
        assert_eq!(std::fs::read_to_string(home.join("keys.json")).unwrap(), "{\"global\": {}}");
        assert_eq!(std::fs::read_to_string(home.join("keys.json.shipped")).unwrap(), shipped("keys.json").unwrap());
        for (path, contents, always) in DEFAULTS {
            assert!(*always || !PREVIOUS.contains(&(*path, fnv(contents.as_bytes()))), "{path} is listed as an old version");
        }
        std::fs::remove_dir_all(&home).unwrap();
    }
}

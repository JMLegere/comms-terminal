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
    ("icons/calendar-days.svg", include_str!("../defaults/icons/calendar-days.svg"), true),
    ("icons/car.svg", include_str!("../defaults/icons/car.svg"), true),
    ("icons/circle-help.svg", include_str!("../defaults/icons/circle-help.svg"), true),
    ("icons/handshake.svg", include_str!("../defaults/icons/handshake.svg"), true),
    ("icons/hourglass.svg", include_str!("../defaults/icons/hourglass.svg"), true),
    ("icons/link.svg", include_str!("../defaults/icons/link.svg"), true),
    ("icons/refresh-cw.svg", include_str!("../defaults/icons/refresh-cw.svg"), true),
    ("icons/timer.svg", include_str!("../defaults/icons/timer.svg"), true),
    ("icons/triangle-alert.svg", include_str!("../defaults/icons/triangle-alert.svg"), true),
    ("icons/user.svg", include_str!("../defaults/icons/user.svg"), true),
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
    ("icons/win-min.svg", include_str!("../defaults/icons/win-min.svg"), true), // herdr-app mods/tabs (Apache-2.0)
    ("icons/win-max.svg", include_str!("../defaults/icons/win-max.svg"), true), // herdr-app mods/tabs (Apache-2.0)
    ("icons/win-close.svg", include_str!("../defaults/icons/win-close.svg"), true), // herdr-app mods/tabs (Apache-2.0)
    ("views/threads/main.js", include_str!("../defaults/views/threads/main.js"), true),
    ("views/thread/main.js", include_str!("../defaults/views/thread/main.js"), true),
    ("views/thread/composer.js", include_str!("../defaults/views/composer.js"), true), // the one Composer, beside each view that imports it
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
    ("views/case/composer.js", include_str!("../defaults/views/composer.js"), true), // the one Composer, beside each view that imports it
    ("views/cases/main.js", include_str!("../defaults/views/cases/main.js"), true),
    ("views/channels/main.js", include_str!("../defaults/views/channels/main.js"), true),
    ("views/connection/main.js", include_str!("../defaults/views/connection/main.js"), true),
    ("views/context/main.js", include_str!("../defaults/views/context/main.js"), true),
    ("views/draft/main.js", include_str!("../defaults/views/draft/main.js"), true),
    ("views/draft/composer.js", include_str!("../defaults/views/composer.js"), true), // the one Composer, beside each view that imports it
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
    ("views/settings/main.js", include_str!("../defaults/views/settings/main.js"), true),
    ("views/issues/main.js", include_str!("../defaults/views/issues/main.js"), true),
    ("views/promises/main.js", include_str!("../defaults/views/promises/main.js"), true),
    ("views/issue/main.js", include_str!("../defaults/views/issue/main.js"), true),
    ("views/actions/main.js", include_str!("../defaults/views/actions/main.js"), true),
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
    ("layouts/main.json", 0x67fba9630500596c),
    ("layouts/main.json", 0xb363995d330f6414),
    ("layouts/main.json", 0x4e2034e653395857),
    ("layouts/main.json", 0x085901ebea1c6a70),
    ("layouts/main.json", 0xc502fe9bbb4bdf18),
    ("keys.json", 0x1f4eb1ede7a5640e),
    ("keys.json", 0xb41b1cc21ba85367),
    ("keys.json", 0x425cfe7966802582),
    ("keys.json", 0x4cf57e992252d03b),
    ("keys.json", 0xe0ffd191c9049f42),
    ("keys.json", 0x2f488345fba78d9d),
    ("keys.json", 0x8628f3383aace4af),
    ("keys.json", 0x515d848d3bd681db),
    ("keys.json", 0xb4f6fe76e1ac3b7d),
    ("keys.json", 0xb4244a89c7ab0dd3),    ("layouts/main.json", 0x486a0e4aec1ea574),
    ("keys.json", 0x8fad973448474b78),
    ("layouts/main.json", 0x471a72fca14e6470),
    ("keys.json", 0xcce20263f132a473),
    ("layouts/main.json", 0x9b113bf534c92f9a),
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

    const MAIN_LAYOUT: &str = "layouts/main.json";
    const SHIPPED_LAYOUT: &str = "layouts/main.json.shipped";

    struct Scratch(PathBuf);

    impl Scratch {
        fn new(case: &str) -> Self {
            let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
            let home = std::env::temp_dir().join(format!("ct-app-layout-{case}-{}-{stamp}", std::process::id()));
            std::fs::create_dir(&home).unwrap();
            Self(home)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn load_layout(home: &Path, path: &str) -> crate::layout::Node {
        crate::layout::parse(&read_json(home, path).unwrap()).unwrap()
    }

    fn assert_stock_routes(node: &crate::layout::Node) {
        let list = node.find("list").unwrap();
        let detail = node.find("detail").unwrap();
        assert_eq!(list.view_on(Some("today"), Some("promise")), Some("today"));
        assert_eq!(detail.view_on(Some("today"), Some("promise")), Some("side"));
        assert_eq!(detail.view_on(Some("today"), Some("task")), Some("task"));
        assert_eq!(detail.view_on(Some("today"), Some("issue")), Some("issue"));
        assert_eq!(detail.view_on(Some("today"), Some("thread")), Some("thread"));
        assert_eq!(detail.view_on(Some("calendar"), None), Some("day"));
        assert_eq!(list.view_on(Some("promises"), None), Some("promises"));
        assert_eq!(detail.view_on(Some("promises"), None), Some("side"));
    }

    #[test]
    fn today_layout_upgrades_exact_6223b99_stock() {
        let scratch = Scratch::new("old");
        let home = &scratch.0;
        let path = home.join(MAIN_LAYOUT);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        assert_eq!(fnv(LAYOUT_6223B99.as_bytes()), 0x9b113bf534c92f9a);
        std::fs::write(&path, LAYOUT_6223B99).unwrap();
        let old = load_layout(home, MAIN_LAYOUT);
        assert_eq!(old.find("detail").unwrap().view_on(Some("today"), Some("promise")), Some("thread"));

        let preferences = [
            ("keys.json", "{\"global\": {\"t\": \"nav.today\"}}\n"),
            ("settings.json", "{\"ui_font\": \"monospace\"}\n"),
        ];
        for (file, bytes) in preferences {
            std::fs::write(home.join(file), bytes).unwrap();
        }
        write(home).unwrap();

        assert_stock_routes(&load_layout(home, MAIN_LAYOUT));
        assert_eq!(std::fs::read_to_string(&path).unwrap(), shipped(MAIN_LAYOUT).unwrap());
        assert!(!home.join(SHIPPED_LAYOUT).exists());
        for (file, bytes) in preferences {
            assert_eq!(std::fs::read_to_string(home.join(file)).unwrap(), bytes);
        }
    }

    #[test]
    fn today_layout_installs_fresh_and_leaves_current_stock_alone() {
        let scratch = Scratch::new("fresh");
        let home = &scratch.0;
        let path = home.join(MAIN_LAYOUT);
        write(home).unwrap();
        assert_stock_routes(&load_layout(home, MAIN_LAYOUT));
        assert_eq!(std::fs::read_to_string(&path).unwrap(), shipped(MAIN_LAYOUT).unwrap());
        assert!(!home.join(SHIPPED_LAYOUT).exists());

        let file = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
        file.set_modified(std::time::UNIX_EPOCH).unwrap();
        let before = file.metadata().unwrap().modified().unwrap();
        write(home).unwrap();
        assert_eq!(std::fs::metadata(&path).unwrap().modified().unwrap(), before);
        assert_stock_routes(&load_layout(home, MAIN_LAYOUT));
        assert!(!home.join(SHIPPED_LAYOUT).exists());
    }

    #[test]
    fn today_layout_keeps_edits_and_stages_the_stock_candidate() {
        let custom_route = LAYOUT_6223B99.replace(
            r#""today:issue": "issue""#,
            "\"today:issue\": \"issue\",\n                \"today:custom\": \"custom-panel\"",
        );
        for (case, edited, custom_view) in [
            ("whitespace", format!("{LAYOUT_6223B99}\n"), "thread"),
            ("custom", custom_route, "custom-panel"),
        ] {
            let scratch = Scratch::new(case);
            let home = &scratch.0;
            let path = home.join(MAIN_LAYOUT);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, &edited).unwrap();
            write(home).unwrap();

            assert_eq!(std::fs::read_to_string(&path).unwrap(), edited);
            assert_eq!(std::fs::read_to_string(home.join(SHIPPED_LAYOUT)).unwrap(), shipped(MAIN_LAYOUT).unwrap());
            assert_stock_routes(&load_layout(home, SHIPPED_LAYOUT));
            let node = load_layout(home, MAIN_LAYOUT);
            let detail = node.find("detail").unwrap();
            assert_eq!(detail.view_on(Some("today"), Some("promise")), Some("thread"));
            assert_eq!(detail.view_on(Some("today"), Some("custom")), Some(custom_view));
        }
    }

    // Exact git blob 1c7ef3b8a6b86d3fa02997901245e690d5925ad1 from 6223b99:app/defaults/layouts/main.json.
    // Copy the bytes (including final LF) so this test also runs in source archives and shallow clones.
    const LAYOUT_6223B99: &str = r#"{
  "id": "root",
  "type": "row",
  "background": "canvas",
  "children": [
    {
      "id": "sidebar",
      "type": "column",
      "size": {
        "px": 248
      },
      "background": "chrome",
      "border": {
        "side": "right",
        "color": "border"
      },
      "children": [
        {
          "id": "brand",
          "type": "block",
          "size": {
            "px": 46
          },
          "view": "brand"
        },
        {
          "id": "rail",
          "type": "block",
          "view": "nav"
        }
      ]
    },
    {
      "id": "work",
      "type": "column",
      "children": [
        {
          "id": "topbar",
          "type": "block",
          "size": {
            "px": 42
          },
          "view": "topbar"
        },
        {
          "id": "work-body",
          "type": "row",
          "padding": 8,
          "gap": 8,
          "children": [
            {
              "id": "list",
              "type": "block",
              "size": {
                "px": 460
              },
              "view": "threads",
              "card": true,
              "pages": {
                "today": "today",
                "calendar": "calendar",
                "drafts": "drafts",
                "cases": "cases",
                "tasks": "tasks",
                "sequences": "sequences",
                "pipeline": null,
                "people": "people",
                "organizations": "organizations",
                "channels": "channels",
                "person": null,
                "organization": null,
                "connection": null,
                "context": null,
                "event": null,
                "case": null,
                "issues": "issues",
                "actions": "actions"
              }
            },
            {
              "id": "detail",
              "type": "block",
              "view": "thread",
              "card": true,
              "pages": {
                "today": "thread",
                "calendar": "day",
                "drafts": "draft",
                "drafts:thread": "thread",
                "cases": "case",
                "tasks": "task",
                "sequences": "sequence",
                "pipeline": "pipeline",
                "people": "person",
                "organizations": "organization",
                "channels": "connection",
                "today:task": "task",
                "tasks:draft": "draft",
                "tasks:thread": "thread",
                "today:draft": "draft",
                "today:paused": "side",
                "today:unattended": "side",
                "person": "person",
                "organization": "organization",
                "connection": "connection",
                "context": "context",
                "event": "event",
                "case": "case",
                "issues": "issue",
                "actions": null,
                "actions:draft": "draft",
                "actions:thread": "thread",
                "actions:task": "task",
                "actions:person": "person",
                "today:issue": "issue"
              }
            }
          ]
        },
        {
          "id": "statusbar",
          "type": "block",
          "size": {
            "px": 28
          },
          "view": "status"
        }
      ]
    }
  ]
}
"#;
}

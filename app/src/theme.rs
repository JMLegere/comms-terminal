//! The theme: `settings.json` names it, `themes/<name>.json` holds its colour tokens. Views read the tokens
//! through `theme()`; the host's layout (`color`) and gpui-base's controls use the same colours.

use std::path::Path;

use serde_json::{Value, json};

use crate::{host, workspace};

thread_local! {
    static FONT: std::cell::RefCell<Option<String>> = const { std::cell::RefCell::new(None) };
}

/// The UI font settings.json names (herdr-app's `ui_font`), if any.
pub fn font() -> Option<String> {
    FONT.with(|f| f.borrow().clone())
}

/// A theme token (`#rrggbb` or `#rrggbbaa`) as a colour; transparent when the theme lacks it.
pub fn color(token: &str) -> gpui::Rgba {
    let value = host::theme_value(token);
    let hex = value.as_deref().unwrap_or("#00000000").trim_start_matches('#');
    let n = u32::from_str_radix(hex, 16).unwrap_or(0);
    gpui::rgba(if hex.len() == 6 { (n << 8) | 0xff } else { n })
}

/// Reads the chosen theme (the shipped light theme when the choice is unusable) and applies it everywhere.
pub fn apply(home: &Path, cx: &mut gpui::App) {
    let name = workspace::read_json(home, "settings.json")
        .ok()
        .and_then(|s| s.get("theme").and_then(Value::as_str).map(str::to_owned))
        .unwrap_or_else(|| "gruvbox-material-light".into());
    let tokens = workspace::read_json(home, &format!("themes/{name}.json"))
        .or_else(|_| serde_json::from_str(workspace::shipped("themes/gruvbox-material-light.json").unwrap()).map_err(|e| e.to_string()))
        .unwrap_or(Value::Null);
    let settings = workspace::read_json(home, "settings.json").unwrap_or(Value::Null);
    FONT.with(|f| *f.borrow_mut() = settings.get("ui_font").and_then(Value::as_str).map(str::to_owned));
    host::set_theme(tokens.clone());
    let g = |k: &str| tokens.get(k).cloned().unwrap_or(Value::Null);
    let palette = json!({
        "background": g("background"), "foreground": g("foreground"),
        "surface": g("surface"), "surface_foreground": g("foreground"),
        "primary": g("accent"), "primary_foreground": g("background"),
        "secondary": g("raised"), "secondary_foreground": g("foreground"),
        "muted": g("raised"), "muted_foreground": g("muted"),
        "accent": g("hover"), "accent_foreground": g("foreground"),
        "destructive": g("danger"), "destructive_foreground": g("background"),
        "border": g("border"), "input": g("border"), "ring": g("accent"),
        "selection": g("selection"),
    });
    if let Ok(colors) = serde_json::from_value::<gpui_base::ColorTokens>(palette) {
        let t = gpui_base::Theme::global_mut(cx);
        t.appearance = if tokens.get("appearance").and_then(Value::as_str) == Some("light") {
            gpui_base::ThemeAppearance::Light
        } else {
            gpui_base::ThemeAppearance::Dark
        };
        t.tokens.colors = colors;
    }
}

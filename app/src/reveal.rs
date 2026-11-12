//! A click on a stored Attachment's chip (FR-A5): save the file in Downloads, then show it selected in the file
//! manager (Files on Linux, Finder on macOS). Downloads keeps an identical earlier copy rather than adding another.

use anyhow::{bail, Context, Result};
use std::path::{Path, PathBuf};
use std::process::Command;

/// Save the Attachment at `url` as `name` in Downloads and select it in the file manager; returns where it is.
pub fn reveal(url: &str, name: &str) -> Result<PathBuf> {
    if !url.starts_with("http://") && !url.starts_with("https://") {
        bail!("not a file link");
    }
    let dir = downloads();
    std::fs::create_dir_all(&dir).with_context(|| format!("cannot create {}", dir.display()))?;
    let out = Command::new("curl").args(["-fsSL", "--max-time", "120", url]).output().context("curl did not run")?;
    if !out.status.success() {
        bail!("download failed");
    }
    let (path, existing) = target(&dir, &safe_name(name), &out.stdout);
    if !existing {
        std::fs::write(&path, &out.stdout).with_context(|| format!("cannot write {}", path.display()))?;
    }
    show(&path)?;
    Ok(path)
}

/// The Downloads folder: the desktop's own setting, else ~/Downloads.
fn downloads() -> PathBuf {
    if let Ok(out) = Command::new("xdg-user-dir").arg("DOWNLOAD").output() {
        let dir = String::from_utf8_lossy(&out.stdout).trim().to_owned();
        if out.status.success() && !dir.is_empty() {
            return PathBuf::from(dir);
        }
    }
    PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join("Downloads")
}

/// The file name alone: no folders, no leading dots; "attachment" when nothing is left.
pub fn safe_name(name: &str) -> String {
    let base = name.rsplit(['/', '\\']).next().unwrap_or("").trim().trim_start_matches('.');
    let clean: String = base.chars().filter(|c| !c.is_control()).collect();
    if clean.is_empty() { "attachment".into() } else { clean }
}

/// Where the bytes go: `name`, or the first free `name (2)`, `name (3)`… An existing file with the same bytes is
/// reused, so a second click selects the copy already there (`true`).
pub fn target(dir: &Path, name: &str, bytes: &[u8]) -> (PathBuf, bool) {
    let (stem, ext) = match name.rfind('.') {
        Some(i) if i > 0 => (&name[..i], &name[i..]),
        _ => (name, ""),
    };
    for n in 1.. {
        let path = if n == 1 { dir.join(name) } else { dir.join(format!("{stem} ({n}){ext}")) };
        match std::fs::read(&path) {
            Ok(old) if old == bytes => return (path, true),
            Ok(_) => continue,
            Err(_) if path.exists() => continue,
            Err(_) => return (path, false),
        }
    }
    unreachable!()
}

/// Select the file in the file manager.
fn show(path: &Path) -> Result<()> {
    #[cfg(target_os = "macos")]
    let ok = Command::new("open").arg("-R").arg(path).status().map(|s| s.success()).unwrap_or(false);
    #[cfg(not(target_os = "macos"))]
    let ok = Command::new("gdbus")
        .args(["call", "--session", "--dest", "org.freedesktop.FileManager1", "--object-path",
               "/org/freedesktop/FileManager1", "--method", "org.freedesktop.FileManager1.ShowItems"])
        .arg(format!("['{}']", file_uri(path)))
        .arg("")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
        // No file manager service: open the folder instead.
        || Command::new("xdg-open").arg(path.parent().unwrap_or(path)).status().map(|s| s.success()).unwrap_or(false);
    if !ok {
        bail!("saved to {}, but no file manager opened", path.display());
    }
    Ok(())
}

/// A `file://` URI with every byte outside the unreserved set percent-encoded (quotes too, for gdbus).
pub fn file_uri(path: &Path) -> String {
    let mut out = String::from("file://");
    for b in path.to_string_lossy().bytes() {
        if b.is_ascii_alphanumeric() || b"/-_.~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_lose_folders_and_leading_dots() {
        assert_eq!(safe_name("../../etc/passwd"), "passwd");
        assert_eq!(safe_name("C:\\x\\deck.pdf"), "deck.pdf");
        assert_eq!(safe_name(".bashrc"), "bashrc");
        assert_eq!(safe_name("  "), "attachment");
    }

    #[test]
    fn a_second_click_reuses_the_same_file_and_a_different_file_gets_a_number() {
        let dir = std::env::temp_dir().join(format!("reveal-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let (first, existing) = target(&dir, "deck.pdf", b"one");
        assert_eq!((first.file_name().unwrap().to_str().unwrap(), existing), ("deck.pdf", false));
        std::fs::write(&first, b"one").unwrap();
        assert_eq!(target(&dir, "deck.pdf", b"one"), (first.clone(), true));
        let (second, existing) = target(&dir, "deck.pdf", b"two");
        assert_eq!((second.file_name().unwrap().to_str().unwrap(), existing), ("deck (2).pdf", false));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn uris_escape_spaces_and_quotes() {
        assert_eq!(file_uri(Path::new("/home/j/Downloads/Bob's deck.pdf")), "file:///home/j/Downloads/Bob%27s%20deck.pdf");
    }
}

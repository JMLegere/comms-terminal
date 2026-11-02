//! `layouts/<name>.json`: a tree of rows, columns and blocks; a block shows one view or Panel by name.

use serde_json::Value;

#[derive(Clone, Debug, PartialEq)]
pub enum Size {
    Px(f32),
    Fill,
}

#[derive(Clone, Debug)]
pub struct Node {
    pub id: String, // named in errors and, later, in layout commands
    pub kind: Kind,
    pub size: Size,
    pub view: Option<String>,
    /// `"pages": {"drafts": "drafts", …}`: the view shown while `shared("page")` names that page; `view` otherwise.
    pub pages: Vec<(String, String)>,
    pub children: Vec<Node>,
    /// Look, in herdr-app's terms: a theme token behind the node, one bordered side (or all, for a card),
    /// inner padding and the gap between children. A card is a rounded, bordered block on the canvas.
    pub background: Option<String>,
    pub border: Option<(String, String)>, // (side: left | right | top | bottom | all, colour token)
    pub padding: f32,
    pub gap: f32,
    pub card: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Kind {
    Row,
    Column,
    Block,
}

pub fn parse(v: &Value) -> Result<Node, String> {
    let id = v.get("id").and_then(Value::as_str).unwrap_or("").to_string();
    let kind = match v.get("type").and_then(Value::as_str) {
        Some("row") => Kind::Row,
        Some("column") => Kind::Column,
        Some("block") | None => Kind::Block,
        Some(other) => return Err(format!("{id}: unknown type {other:?} (row, column or block)")),
    };
    let size = match v.get("size").and_then(|s| s.get("px")).and_then(Value::as_f64) {
        Some(px) if px > 0.0 => Size::Px(px as f32),
        Some(_) => return Err(format!("{id}: size.px must be positive")),
        None => Size::Fill,
    };
    let view = v.get("view").and_then(Value::as_str).map(str::to_owned);
    if kind == Kind::Block && view.is_none() {
        return Err(format!("block {id:?} names no view"));
    }
    let pages = match v.get("pages") {
        None => Vec::new(),
        Some(Value::Object(m)) => m
            .iter()
            .map(|(page, view)| view.as_str().map(|v| (page.clone(), v.to_owned())).ok_or_else(|| format!("{id}: pages.{page} must name a view")))
            .collect::<Result<Vec<_>, _>>()?,
        Some(_) => return Err(format!("{id}: pages must map page names to views")),
    };
    let children = match v.get("children") {
        Some(Value::Array(items)) => items.iter().map(parse).collect::<Result<Vec<_>, _>>()?,
        Some(_) => return Err(format!("{id}: children must be a list")),
        None => Vec::new(),
    };
    let token = |key: &str| v.get(key).and_then(Value::as_str).map(str::to_owned);
    let border = match v.get("border") {
        None => None,
        Some(b) => {
            let side = b.get("side").and_then(Value::as_str).unwrap_or("all");
            if !["left", "right", "top", "bottom", "all"].contains(&side) {
                return Err(format!("{id}: border.side must be left, right, top, bottom or all"));
            }
            Some((side.to_owned(), b.get("color").and_then(Value::as_str).unwrap_or("border").to_owned()))
        }
    };
    let number = |key: &str| v.get(key).and_then(Value::as_f64).unwrap_or(0.0) as f32;
    let card = v.get("card").and_then(Value::as_bool).unwrap_or(false);
    Ok(Node { id, kind, size, view, pages, children, background: token("background"), border, padding: number("padding"), gap: number("gap"), card })
}

impl Node {
    /// The view this block shows on `page`, where `detail` (`shared("detail")`) may pick a page's variant: a block
    /// with `"today:task"` shows that view while Today's cursor is on a Task, and `"today"` otherwise.
    pub fn view_on(&self, page: Option<&str>, detail: Option<&str>) -> Option<&str> {
        let find = |key: &str| self.pages.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_str());
        page.and_then(|p| detail.and_then(|d| find(&format!("{p}:{d}"))).or_else(|| find(p))).or(self.view.as_deref())
    }

    /// Every view the layout can show, once each, in order.
    pub fn views(&self) -> Vec<String> {
        let mut out = Vec::new();
        self.collect(&mut out);
        out
    }

    fn collect(&self, out: &mut Vec<String>) {
        for v in self.view.iter().chain(self.pages.iter().map(|(_, v)| v)) {
            if !out.contains(v) {
                out.push(v.clone());
            }
        }
        for c in &self.children {
            c.collect(out);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_the_shipped_layout() {
        let v: Value = serde_json::from_str(include_str!("../defaults/layouts/main.json")).unwrap();
        let node = parse(&v).unwrap();
        let views = node.views();
        assert_eq!(views[..6], ["brand", "nav", "topbar", "threads", "today", "calendar"]);
        for page in ["drafts", "draft", "cases", "case", "people", "person", "organization", "pipeline", "sequence", "event", "status"] {
            assert!(views.contains(&page.to_owned()), "{page}");
        }
        assert_eq!(node.background.as_deref(), Some("canvas"));
        assert!(node.children[1].children[1].children.iter().all(|c| c.card));
    }

    #[test]
    fn a_page_and_its_detail_choose_the_view() {
        let node = parse(&json!({"id": "x", "view": "threads", "pages": {"today": "thread", "today:task": "task"}})).unwrap();
        assert_eq!(node.view_on(None, None), Some("threads"));
        assert_eq!(node.view_on(Some("today"), None), Some("thread"));
        assert_eq!(node.view_on(Some("today"), Some("task")), Some("task"));
        assert_eq!(node.view_on(Some("drafts"), Some("task")), Some("threads"));
        assert_eq!(node.views(), ["threads", "thread", "task"]);
    }

    #[test]
    fn refuses_a_block_without_a_view() {
        assert!(parse(&json!({"id": "x", "type": "block"})).is_err());
        assert!(parse(&json!({"id": "x", "type": "grid"})).is_err());
    }
}

//! Message bodies (FR-8): the Local core's sanitized HTML (`message.body`), laid out by litehtml at the width the
//! view gives it. litehtml fetches nothing: images arrive only as the `data:` URIs the core already inlined, so a
//! body makes no network request (NFR-4). Views place it as `MessageBody(key, html)` from "gpui-component".
//!
//! litehtml runs on one worker thread: a heavy newsletter takes up to half a second to lay out (Phase 0), and the
//! window must keep moving between Threads meanwhile (NFR-1). The window reads what the worker finished (each
//! body's height and painted tiles), asks for what is missing, and paints again when it arrives. The page is
//! painted in tiles as it scrolls into view, so a long newsletter costs a few screens of pixels. Ctrl+click (Cmd+click
//! on macOS) on a link opens it in the default browser (FR-9, D44), through litehtml's own anchor handling.

use std::collections::{HashMap, HashSet};
use std::sync::mpsc::{Receiver, Sender, channel};
use std::sync::{Arc, Mutex, OnceLock};

use gpui::{
    App, Bounds, CursorStyle, DispatchPhase, Element, ElementId, GlobalElementId, Hitbox, HitboxBehavior,
    InspectorElementId, IntoElement, LayoutId, MouseButton, MouseMoveEvent, MouseUpEvent, ParentElement, Pixels,
    RenderImage, SharedString, Size, Style, Styled, Window, div, px, relative,
};
use gpui_shell::{
    ArgumentDescriptor, ArgumentSchema, ComponentArgument, ComponentDescriptor, ComponentMaterializer,
    ComponentPayload, ComponentRegistry, ConstructorDescriptor, FrozenComponentRegistry, MaterializeRequest,
};
use litehtml::pixbuf::PixbufContainer;

use crate::host::{self, Request};

/// Added after the mail's own styles. Unlike litehtml's email preset it leaves links and paragraph margins
/// alone: the preset's `a { color: inherit }` and `p { margin: 0 }` hid links and ran paragraphs together
/// (Phase 0).
const MAIL_CSS: &str = "body { margin: 0; word-wrap: break-word; overflow-wrap: break-word; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; }";

const TILE: f32 = 512.; // logical px per painted tile
const KEPT: usize = 16; // laid-out bodies the worker keeps: more than one Thread shows at once
const NEWEST: usize = 4; // layouts the worker takes from a backlog: the rest belong to Threads already left

/// The "gpui-component" catalog: `MessageBody` only.
pub fn components() -> FrozenComponentRegistry {
    let mut registry =
        ComponentRegistry::new(gpui_shell::COMPONENT_REGISTRY_API_VERSION, gpui_shell::DEFAULT_COMPONENT_MODULE)
            .expect("the catalog opens");
    registry
        .register(
            ComponentDescriptor::new("MessageBody", Arc::new(Materializer))
                .with_constructors(vec![ConstructorDescriptor::new(
                    "MessageBody",
                    vec![
                        ArgumentDescriptor::new("key", ArgumentSchema::String),
                        ArgumentDescriptor::new("html", ArgumentSchema::String),
                    ],
                    |args| match args {
                        [ComponentArgument::String(key), ComponentArgument::String(html)] => {
                            Ok(ComponentPayload::new((key.clone(), Arc::<str>::from(html.as_str()))))
                        }
                        _ => Err("MessageBody(key, html) takes two strings".into()),
                    },
                )])
                .with_methods(Vec::new())
                .with_documentation(
                    "A message's HTML from `message.body`, laid out by litehtml. `key` names the body (the message \
                     id, plus whether images are loaded); a new key lays it out again.",
                ),
        )
        .expect("MessageBody registers");
    registry.freeze().expect("the catalog freezes")
}

struct Materializer;

impl ComponentMaterializer for Materializer {
    fn materialize(&self, request: MaterializeRequest<'_>) -> anyhow::Result<gpui::AnyElement> {
        let (key, html) = request
            .payload()
            .downcast_ref::<(String, Arc<str>)>()
            .ok_or_else(|| anyhow::anyhow!("MessageBody payload"))?
            .clone();
        request.finish(div().w_full().child(BodyElement { key: key.into(), html }))
    }
}

// --- the worker's side ---------------------------------------------------------------------------------------

/// One laid-out body, owned by the worker. litehtml's document borrows its container for its whole life, so both
/// live here: the container is boxed and the document is dropped first.
struct Body {
    doc: Option<litehtml::Document<'static>>,
    container: *mut PixbufContainer,
    width: f32,
    height: f32,
    scale: f32,
    pointer: bool,
}

impl Body {
    fn new(html: &str, scale: f32) -> Option<Self> {
        let refuse = |_: &str| -> Option<Vec<u8>> { None }; // NFR-4: no fetch, ever
        let prepared = litehtml::email::prepare_email_html(html.as_bytes(), None, Some(&refuse));
        // Fonts are made at the scale given here, so a body is laid out again when the window's scale changes.
        let mut container = Box::new(PixbufContainer::new_with_scale(1, 1, scale));
        for (uri, bytes) in &prepared.images {
            container.load_image_data(uri, bytes);
        }
        let container = Box::into_raw(container);
        // SAFETY: the container lives until `drop`, which drops the document first. The worker touches the
        // container only between document calls, as `Document::with_container_mut` does.
        let doc = litehtml::Document::from_html(&prepared.html, unsafe { &mut *container }, None, Some(MAIL_CSS));
        match doc {
            Ok(doc) => Some(Self { doc: Some(doc), container, width: 0., height: 0., scale, pointer: false }),
            Err(_) => {
                drop(unsafe { Box::from_raw(container) });
                None
            }
        }
    }

    fn container(&mut self) -> &mut PixbufContainer {
        unsafe { &mut *self.container }
    }

    /// Lays out at `width`; returns the height.
    fn layout(&mut self, width: f32) -> f32 {
        if (width - self.width).abs() >= 0.5 {
            let doc = self.doc.as_mut().expect("document");
            let _ = doc.render(width);
            (self.width, self.height) = (width, doc.height().ceil().max(1.));
        }
        self.height
    }

    /// Paints tile `index` (TILE logical px from `index * TILE` down), as BGRA for GPUI.
    fn tile(&mut self, index: u32) -> Arc<RenderImage> {
        let (width, top, scale) = (self.width.ceil() as u32, index as f32 * TILE, self.scale);
        self.container().resize_with_scale(width, TILE as u32, scale);
        let clip = litehtml::Position { x: 0., y: 0., width: width as f32, height: TILE };
        self.doc.as_mut().expect("document").draw(litehtml::DrawContext::default(), 0., -top, Some(clip));
        let c = self.container();
        let (w, h) = (c.width(), c.height()); // physical
        let mut px = c.pixels().to_vec();
        for p in px.chunks_exact_mut(4) {
            // premultiplied RGBA over the card's white, to the BGRA GPUI uploads
            let inv = 255 - p[3];
            let (r, g, b) = (p[0].saturating_add(inv), p[1].saturating_add(inv), p[2].saturating_add(inv));
            p.copy_from_slice(&[b, g, r, 255]);
        }
        let frame = image::Frame::new(image::RgbaImage::from_raw(w, h, px).expect("tile size"));
        Arc::new(RenderImage::new(vec![frame]))
    }

    /// A plain click at (x, y) in the body: the link under it, if any.
    fn click(&mut self, x: f32, y: f32) -> Option<String> {
        let doc = self.doc.as_mut()?;
        doc.on_lbutton_down(x, y, x, y);
        doc.on_lbutton_up(x, y, x, y);
        self.container().take_anchor_click()
    }

    /// Hover at (x, y): whether the pointer is a hand.
    fn hover(&mut self, x: f32, y: f32) -> bool {
        if let Some(doc) = self.doc.as_mut() {
            doc.on_mouse_over(x, y, x, y);
        }
        self.pointer = self.container().cursor() == "pointer";
        self.pointer
    }
}

impl Drop for Body {
    fn drop(&mut self) {
        self.doc = None;
        drop(unsafe { Box::from_raw(self.container) });
    }
}

enum Job {
    Layout { key: SharedString, html: Arc<str>, width: f32, scale: f32 },
    Tile { key: SharedString, index: u32 },
    Click { key: SharedString, x: f32, y: f32 },
    Hover { key: SharedString, x: f32, y: f32 },
}

/// What the window may read: each body as the worker last finished it.
#[derive(Default)]
struct Shown {
    width: f32,
    scale: f32,
    height: f32,
    tiles: HashMap<u32, Arc<RenderImage>>,
    pointer: bool,
}

#[derive(Default)]
struct Board {
    shown: HashMap<SharedString, Shown>,
    asked: HashSet<String>,       // jobs sent and not yet done, so a frame does not ask twice
    stale: Vec<Arc<RenderImage>>, // tiles to free from the sprite atlas at the next paint
}

static BOARD: Mutex<Option<Board>> = Mutex::new(None);
static JOBS: OnceLock<Mutex<Sender<Job>>> = OnceLock::new();

fn board<R>(f: impl FnOnce(&mut Board) -> R) -> R {
    f(BOARD.lock().unwrap().get_or_insert_with(Board::default))
}

fn send(job: Job) {
    let jobs = JOBS.get_or_init(|| {
        let (tx, rx) = channel();
        std::thread::Builder::new().name("message-bodies".into()).spawn(move || work(rx)).expect("worker");
        Mutex::new(tx)
    });
    let _ = jobs.lock().unwrap().send(job);
}

/// Sends `job` unless the same one is already on its way.
fn ask(signature: String, job: Job) {
    if board(|b| b.asked.insert(signature)) {
        send(job);
    }
}

fn layout_signature(key: &str, width: f32, scale: f32) -> String {
    format!("L {key} {} {scale}", width.round())
}

fn tile_signature(key: &str, width: f32, index: u32) -> String {
    format!("T {key} {} {index}", width.round())
}

fn work(jobs: Receiver<Job>) {
    let mut bodies: Vec<(SharedString, Body)> = Vec::new(); // most recent last
    while let Ok(first) = jobs.recv() {
        let mut batch: Vec<Job> = std::iter::once(first).chain(jobs.try_iter()).collect();
        // A backlog means Jeremy moved on: lay out only the newest bodies; the window asks again for any it shows.
        let mut kept = Vec::new();
        for job in batch.iter().rev() {
            if let Job::Layout { key, .. } = job {
                if !kept.contains(key) && kept.len() < NEWEST {
                    kept.push(key.clone());
                }
            }
        }
        batch.retain(|job| match job {
            Job::Layout { key, html: _, width, scale } if !kept.contains(key) => {
                board(|b| b.asked.remove(&layout_signature(key, *width, *scale)));
                false
            }
            _ => true,
        });
        for job in batch {
            run(job, &mut bodies);
            let _ = host::request(Request::Repaint);
        }
    }
}

fn run(job: Job, bodies: &mut Vec<(SharedString, Body)>) {
    let find = |bodies: &mut Vec<(SharedString, Body)>, key: &SharedString| bodies.iter().position(|(k, _)| k == key);
    match job {
        Job::Layout { key, html, width, scale } => {
            if let Some(at) = find(bodies, &key).filter(|&at| bodies[at].1.scale != scale) {
                bodies.remove(at);
            }
            let at = match find(bodies, &key) {
                Some(at) => at,
                None => {
                    if bodies.len() >= KEPT {
                        let (old, _) = bodies.remove(0);
                        board(|b| {
                            if let Some(gone) = b.shown.remove(&old) {
                                b.stale.extend(gone.tiles.into_values());
                            }
                        });
                    }
                    match Body::new(&html, scale) {
                        Some(body) => bodies.push((key.clone(), body)),
                        None => {
                            board(|b| b.shown.insert(key.clone(), Shown { width, scale, height: 0., ..Default::default() }));
                            return;
                        }
                    }
                    bodies.len() - 1
                }
            };
            let entry = bodies.remove(at);
            bodies.push(entry);
            let height = bodies.last_mut().expect("just pushed").1.layout(width);
            board(|b| {
                b.asked.remove(&layout_signature(&key, width, scale));
                let old = b.shown.insert(key, Shown { width, scale, height, ..Default::default() });
                b.stale.extend(old.into_iter().flat_map(|o| o.tiles.into_values()));
            });
        }
        Job::Tile { key, index } => {
            let Some(at) = find(bodies, &key) else { return };
            let body = &mut bodies[at].1;
            let tile = body.tile(index);
            let width = body.width;
            board(|b| {
                b.asked.remove(&tile_signature(&key, width, index));
                match b.shown.get_mut(&key).filter(|s| (s.width - width).abs() < 0.5) {
                    Some(shown) => drop(shown.tiles.insert(index, tile)),
                    None => b.stale.push(tile),
                }
            });
        }
        Job::Click { key, x, y } => {
            if let Some(url) = find(bodies, &key).and_then(|at| bodies[at].1.click(x, y)) {
                let _ = host::request(Request::Open(url));
            }
        }
        Job::Hover { key, x, y } => {
            let Some(at) = find(bodies, &key) else { return };
            let pointer = bodies[at].1.hover(x, y);
            board(|b| {
                b.asked.remove(&format!("H {key}"));
                if let Some(shown) = b.shown.get_mut(&key) {
                    shown.pointer = pointer;
                }
            });
        }
    }
}

// --- the window's side ---------------------------------------------------------------------------------------

struct BodyElement {
    key: SharedString,
    html: Arc<str>,
}

impl IntoElement for BodyElement {
    type Element = Self;
    fn into_element(self) -> Self {
        self
    }
}

impl Element for BodyElement {
    type RequestLayoutState = ();
    type PrepaintState = Hitbox;

    fn id(&self) -> Option<ElementId> {
        None
    }

    fn source_location(&self) -> Option<&'static core::panic::Location<'static>> {
        None
    }

    fn request_layout(
        &mut self,
        _: Option<&GlobalElementId>,
        _: Option<&InspectorElementId>,
        window: &mut Window,
        _: &mut App,
    ) -> (LayoutId, ()) {
        let mut style = Style::default();
        style.size.width = relative(1.).into();
        let (key, html) = (self.key.clone(), self.html.clone());
        let id = window.request_measured_layout(style, move |known, available, window, _| {
            let width = known.width.map(f32::from).unwrap_or(match available.width {
                gpui::AvailableSpace::Definite(w) => w.into(),
                _ => 640.,
            });
            let scale = window.scale_factor();
            // The height the worker last found; until it finishes at this width, the old one (or none) stands.
            let (height, current) = board(|b| match b.shown.get(&key) {
                Some(s) => (s.height, (s.width - width).abs() < 0.5 && s.scale == scale),
                None => (0., false),
            });
            if !current {
                let job = Job::Layout { key: key.clone(), html: html.clone(), width, scale };
                ask(layout_signature(&key, width, scale), job);
            }
            Size { width: px(width), height: px(height) }
        });
        (id, ())
    }

    fn prepaint(
        &mut self,
        _: Option<&GlobalElementId>,
        _: Option<&InspectorElementId>,
        bounds: Bounds<Pixels>,
        _: &mut (),
        window: &mut Window,
        _: &mut App,
    ) -> Hitbox {
        window.insert_hitbox(bounds, HitboxBehavior::Normal)
    }

    fn paint(
        &mut self,
        _: Option<&GlobalElementId>,
        _: Option<&InspectorElementId>,
        bounds: Bounds<Pixels>,
        _: &mut (),
        hitbox: &mut Hitbox,
        window: &mut Window,
        _: &mut App,
    ) {
        let visible = bounds.intersect(&window.content_mask().bounds);
        let (stale, shown) = board(|b| {
            let shown = b.shown.get(&self.key).map(|s| (s.width, s.height, s.pointer, s.tiles.clone()));
            (std::mem::take(&mut b.stale), shown)
        });
        for image in stale {
            let _ = window.drop_image(image);
        }
        let Some((width, height, pointer, tiles)) = shown else { return };
        if visible.size.height > px(0.) && height > 0. && (width - f32::from(bounds.size.width)).abs() < 1. {
            let first = (f32::from(visible.top() - bounds.top()) / TILE).floor().max(0.) as u32;
            let last = ((f32::from(visible.bottom() - bounds.top()) / TILE).ceil() as u32).min((height / TILE).ceil() as u32);
            for index in first..last {
                match tiles.get(&index) {
                    Some(image) => {
                        let at = Bounds::new(
                            gpui::point(bounds.left(), bounds.top() + px(index as f32 * TILE)),
                            gpui::size(bounds.size.width, px(TILE)),
                        );
                        // the last tile clips to the body
                        let _ = window.paint_image(bounds, at, Default::default(), image.clone(), 0, false);
                    }
                    None => ask(tile_signature(&self.key, width, index), Job::Tile { key: self.key.clone(), index }),
                }
            }
        }
        if pointer {
            window.set_cursor_style(CursorStyle::PointingHand, hitbox);
        }

        let (key, origin, hit) = (self.key.clone(), bounds.origin, hitbox.clone());
        window.on_mouse_event(move |event: &MouseMoveEvent, phase, window, _| {
            if phase == DispatchPhase::Bubble && hit.is_hovered(window) {
                let (x, y) = (f32::from(event.position.x - origin.x), f32::from(event.position.y - origin.y));
                ask(format!("H {key}"), Job::Hover { key: key.clone(), x, y });
            }
        });
        let (key, hit) = (self.key.clone(), hitbox.clone());
        window.on_mouse_event(move |event: &MouseUpEvent, phase, window, _| {
            // Ctrl+click (Cmd+click on macOS) opens a link; a plain click only selects (D44, accepted as Ctrl+click).
            let opener = event.modifiers.control || event.modifiers.platform;
            if phase == DispatchPhase::Bubble && event.button == MouseButton::Left && opener && hit.is_hovered(window) {
                let (x, y) = (f32::from(event.position.x - origin.x), f32::from(event.position.y - origin.y));
                send(Job::Click { key: key.clone(), x, y });
            }
        });
    }
}

/// Opens a link in the default browser. Only web and mail links: a `file:` or app link in a message is not
/// something one click should run.
pub fn open(url: &str, cx: &mut App) {
    if openable(url) {
        cx.open_url(url.trim());
    }
}

fn openable(url: &str) -> bool {
    let lower = url.trim().to_ascii_lowercase();
    ["http://", "https://", "mailto:"].iter().any(|s| lower.starts_with(s))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_click_on_a_link_names_it_and_a_click_beside_it_does_not() {
        let html = r#"<p style="margin:0"><a href="https://example.com/x" style="font-size:40px">link</a></p><p style="height:200px">text</p>"#;
        let mut body = Body::new(html, 1.0).expect("lays out");
        assert!(body.layout(400.) > 200.);
        assert_eq!(body.click(10., 20.).as_deref(), Some("https://example.com/x"));
        assert_eq!(body.click(10., 150.), None);
        assert!(body.hover(10., 20.) && !body.hover(10., 150.));
        assert_eq!(body.tile(0).size(0).width.0, 400);
    }

    #[test]
    fn only_web_and_mail_links_open() {
        assert!(openable("HTTPS://a.b") && openable(" mailto:x@y.z"));
        assert!(!openable("file:///etc/passwd") && !openable("javascript:alert(1)"));
    }
}

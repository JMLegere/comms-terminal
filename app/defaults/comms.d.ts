// The `comms` module: what a view or Panel reaches of Comms Terminal (ADR 0013). Written by the App on every
// start; edits here are overwritten.

/** One Local API call (see `rpc.discover`). The App adds your Origin: Panels cannot send, schedule, complete
 * Tasks, undo or Respond. Values use the core's tags ($dt, $dc, $tuple, $bytes, $dict). */
export function call(method: string, params?: Record<string, unknown>): Promise<any>;

/** The next event from the Local core: { seq, kind, data }, where kind is rows, status, toast, update,
 * heartbeat, missed or disconnected. Await it in a loop inside cx.spawn. */
export function nextEvent(): Promise<{ seq: number; kind: string; data: any }>;

/** The next key command addressed to this view ("next" for "threads.next" in keys.json). */
export function nextCommand(): Promise<string>;

/** core.status as last reported: online, problem, build, service_build, schema, pending, seq. */
export function status(): any;

/** The theme's colour tokens (themes/<name>.json, chosen in settings.json). */
export function theme(): Record<string, string>;

/** "view" for the App's shipped views, "panel" for Interface-file Panels. */
export function origin(): "view" | "panel";

/** State shared by every view, such as the selected Thread. Setting it renders every view again. */
export function shared(key: string): any;
export function setShared(key: string, value: unknown): boolean;

/** Open a link in the default browser: an http, https or mailto link, such as an Attachment's `url`. */
export function open(url: string): void;
/** Save a stored Attachment (its signed `url`) as `name` in Downloads, then show it selected in the file manager. */
export function reveal(url: string, name: string): void;

/** Show a timed notification, bottom right, as the core's own toasts show; `timeout` in seconds (5 by default). */
export function toast(message: string, severity?: "information" | "warning" | "error", timeout?: number): void;

/** keys.json as it stands: { "global": { "j": "threads.next" }, "page:<name>": { … } }. */
export function keys(): Record<string, Record<string, string>>;
/** The Module an App command belongs to (ADR 0017): "pipelines", "sequences", "cases", or null for Core. */
export function moduleOf(command: string): string | null;
/** The installed font families (Settings › Look). */
export function fonts(): string[];
/** settings.json as an object. */
export function settings(): Record<string, any>;
/** Writes one settings.json key (null removes it); the App applies it at once. */
export function setting(key: string, value: unknown): boolean;
/** The newest `n` entries of the App's diagnostic log (`data/log.jsonl`), oldest first. */
export function logTail(n?: number): Array<{ t: number; kind: string; [field: string]: any }>;

/** Give the keyboard back to the window after a text field (Escape in a reply box, say), so plain keys act again. */
export function focusMain(): void;

/** Run an App command: "app.reset", or "<view>.<command>". */
export function run(command: string): void;

/** Record a timing mark for `comms-terminal-app probe` (no effect outside a probe). */
export function mark(name: string): void;

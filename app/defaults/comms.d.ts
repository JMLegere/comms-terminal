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

/** Run an App command: "app.reset", or "<view>.<command>". */
export function run(command: string): void;

/** Record a timing mark for `comms-terminal-app probe` (no effect outside a probe). */
export function mark(name: string): void;

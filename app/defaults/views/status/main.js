// The status bar, as herdr-app's workspace-status: online or read-only, the Service's build, and the last toast.
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { nextEvent, status, theme } from "comms";

export default class Status extends View {
  init(_props, cx) {
    this.toast = null;
    cx.spawn(async (cx) => {
      while (true) {
        const e = await nextEvent();
        if (e.kind === "toast") this.toast = e.data;
        if (e.kind === "disconnected") this.toast = { message: "The Local core went away", severity: "error" };
        if (["toast", "status", "update", "disconnected"].includes(e.kind)) cx.notify();
      }
    });
  }

  render() {
    const t = theme();
    const s = status() ?? {};
    const label = (icon, text) => h_flex().gap(6).items_center().child(svg(`icons/${icon}.svg`).size(13)).child(text);
    return h_flex().size_full().px(14).gap(16).items_center().bg(t.chrome).border_t(1).border_color(t.border).text_size(11.8).text_color(t.muted)
      .child(h_flex().gap(6).items_center()
        .child(div().size(6).rounded(3).bg(s.online ? t.status_done : t.danger))
        .child(div().text_color(s.online ? t.muted : t.danger).child(s.online ? "Online" : `Read-only · ${s.problem ?? "offline"}`)))
      .child(label("server", "Oracle"))
      .child(label("git-branch", s.service_build ? `Service ${s.service_build}` : "Service ?"))
      .when(Boolean(s.pending), (el) => el.child(div().child(`${s.pending} saving`)))
      .child(div().flex_1())
      .when(Boolean(this.toast), (el) => el.child(div().truncate().text_color(this.toast?.severity === "error" ? t.danger : t.muted).child(this.toast?.message ?? "")));
  }
}

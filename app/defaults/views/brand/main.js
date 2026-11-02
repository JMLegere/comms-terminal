// The sidebar's top row: the App's mark and name, as herdr-app's workspace-chrome draws its own.
import { View, div } from "gpui-kit";
import { h_flex } from "gpui-base";
import { theme } from "comms";

export default class Brand extends View {
  render() {
    const t = theme();
    return h_flex().size_full().bg(t.chrome).pl(14).pr(10).gap(8).items_center().text_size(13.5)
      .child(div().size(20).rounded(5).flex_none().flex().items_center().justify_center().bg(t.accent)
        .text_size(11.5).font_semibold().text_color(t.accent_foreground).child("CT"))
      .child(div().flex_1().font_semibold().text_color(t.strong ?? t.foreground).child("Comms Terminal"));
  }
}

"""Message bodies (decisions #105-#109): synthetic fixtures shaped like the real mail they were built on."""

from datetime import datetime, timezone
from email.message import EmailMessage

from universal_inbox import email as em, files, inbox, render


def html_mail(html: str, subject="Hello", text=None, **parts) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Ana Ruiz <ana@acme.com>", "jeremy@agentmaple.com", subject
    m["Message-ID"], m["Date"] = "<a1@acme.com>", "Mon, 12 Oct 2026 10:15:00 +0000"
    m.set_content(text or "plain part")
    m.add_alternative(html, subtype="html")
    for name, (data, ctype) in parts.items():
        main, sub = ctype.split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m.as_bytes()


def shown(body):
    return render.plain(body.text)


def test_links_keep_their_label_and_show_the_real_domain():
    html = ('<p>Hi ,</p><a href="https://track.pstmrk.it/3ts/app.attio.com%2Ftasks/abc/1/x">View <b>all</b> tasks</a>'
            '<a href="https://urldefense.proofpoint.com/v2/url?u=https-3A__in.xero.com_kFSX&d=x">Pay now</a>'
            '<p>Join: <a href="https://rbc.zoom.us/j/943?pwd=x">https://rbc.zoom.us/j/943?pwd=x</a></p>')
    b = render.from_raw(html_mail(html))
    assert "View all tasks ↗ app.attio.com" in shown(b)
    assert "Pay now ↗ in.xero.com" in shown(b)
    assert "Join: ↗ rbc.zoom.us" in shown(b)
    assert "[b]" not in shown(b) and "pstmrk" not in shown(b)


def test_hidden_text_tracking_pixels_and_decorative_links_are_dropped():
    html = ('<div style="display: none; max-height:0">preheader junk</div>'
            '<a href="https://t.example/x"><img src="logo.png"></a>'
            '<p>Hello there</p><img src="https://t.example/open.gif" width="1" height="1" alt="x">'
            '<img src="chart.png" alt="Q3 chart">')
    text = shown(render.from_raw(html_mail(html)))
    assert text == "Hello there\n[image: Q3 chart]"


def test_inline_cid_images_leave_a_marker_where_they_sit_and_remote_ones_their_alt():
    html = ('<p>Before</p><a href="https://x.test"><img src="cid:shot1@x"></a><p>After <b>bold <img src="cid:two">'
            ' end</b></p><img src="https://t.example/c.png" alt="Remote chart"><img src="https://t.example/d.png">')
    b = render.from_raw(html_mail(html))
    assert b.text == ("Before\n\n\ue000shot1@x\ue001\n\nAfter [b]bold\n\ue000two\ue001\nend[/b]\n"
                      "[#928374]\\[image: Remote chart][/]")
    assert render.IMG.findall(b.text) == ["shot1@x", "two"]
    assert shown(b) == "Before\n\n[image]\n\nAfter bold\n[image]\nend\n[image: Remote chart]"  # no markers


def test_gmail_quote_and_signature_are_trimmed():
    html = ('<div dir="ltr">Friday works for me!</div><br><div class="gmail_quote"><div class="gmail_attr">'
            'On Fri, Oct 9, 2026 at 8:06 AM Tony &lt;tony@x.com&gt; wrote:<br></div>'
            '<blockquote class="gmail_quote">Hi Jeremy, are you free?</blockquote></div>')
    b = render.from_raw(html_mail(html))
    assert shown(b) == "Friday works for me!"
    assert "are you free?" in render.plain(b.trimmed)


def test_outlook_header_block_is_trimmed():
    html = ("<p>Perfect, all paid up.</p><hr><p><b>From:</b> Jeremy &lt;jeremy@agentmaple.com&gt;<br>"
            "<b>Sent:</b> October 7, 2026 1:27 AM<br><b>To:</b> AP<br><b>Subject:</b> Re: bill</p><p>Hi Jane,</p>")
    b = render.from_raw(html_mail(html))
    assert shown(b).startswith("Perfect, all paid up.")
    assert "Hi Jane" not in shown(b) and "Hi Jane" in render.plain(b.trimmed)


def test_boilerplate_footer_is_trimmed():
    html = ("<p>You have 9 overdue tasks.</p><p>Attio Ltd.<br>42 Saint John's Square, London</p>"
            "<p>©2026 Attio. All rights reserved.</p><p><a href='https://x.io/u'>Unsubscribe</a></p>")
    b = render.from_raw(html_mail(html))
    assert shown(b) == "You have 9 overdue tasks."
    assert "Attio Ltd." in render.plain(b.trimmed) and "Unsubscribe" in render.plain(b.trimmed)


def test_gmail_forward_is_a_nested_card_not_trimmed():
    html = ('<div>FYI, see below.</div><div class="gmail_quote"><div dir="ltr" class="gmail_attr">'
            '---------- Forwarded message ---------<br>From: Bo &lt;bo@acme.com&gt;<br></div>'
            '<div>The contract is attached.</div></div>')
    b = render.from_raw(html_mail(html, subject="Fwd: contract"))
    assert b.trimmed == ""
    assert "│ The contract is attached." in shown(b)
    assert shown(b).startswith("FYI, see below.")


def test_plain_text_labels_and_quotes():
    b = render.from_text("View all tasks ( https://track.pstmrk.it/3ts/app.attio.com%2Fx )\nSee https://docs.acme.com/a?b=1."
                         "\n\nOn Mon, Oct 12, 2026 at 9:00 AM Ana <ana@acme.com> wrote:\n> earlier")
    assert shown(b) == "View all tasks ↗ app.attio.com\nSee ↗ docs.acme.com."
    assert "> earlier" in render.plain(b.trimmed)


def test_invite_card_and_attachments():
    ics = ("BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nSUMMARY:Jeremy <> Kory: Catch Up\r\n"
           "DTSTART:20261014T140000Z\r\nDTEND:20261014T143000Z\r\nORGANIZER;CN=Kory Henn:mailto:k@rbc.com\r\n"
           "ATTENDEE;CN=Jeremy:mailto:jeremy@agentmaple.com\r\nATTENDEE:mailto:k@rbc.com\r\n"
           "LOCATION:https://rbc.zoom.us/j/943\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
    raw = html_mail("<p>Invite</p>", **{"invite.ics": (ics, "text/calendar"), "deck.pdf": (b"%PDF" * 300, "application/pdf")})
    b = render.from_raw(raw)
    assert b.invite["summary"] == "Jeremy <> Kory: Catch Up" and b.invite["guests"] == 2
    card = render.plain(render.invite_card(b.invite))
    assert "Jeremy <> Kory: Catch Up" in card and "Kory Henn · 2 guests" in card and "↗ rbc.zoom.us" in card
    assert b.attachments == [("deck.pdf", "application/pdf", 1200)]
    assert render.chip(*b.attachments[0]) == "deck.pdf · PDF · 1 KB"


def test_raw_message_is_kept_beside_the_database(tmp_path):
    db = inbox.connect(str(tmp_path / "inbox.db"))
    inbox.migrate(db)
    raw = html_mail("<p>hi</p>")
    sha = files.store_raw(db, raw)
    path = tmp_path / "raw" / f"{sha}.eml"
    assert path.read_bytes() == raw and oct(path.stat().st_mode)[-3:] == "600"
    assert files.store_raw(db, raw) == sha  # content-addressed: storing again is a no-op


def test_reply_quotes_the_last_inbound_message_gmail_style():
    history = [{"native_id": "<a1@acme.com>", "content": "Can we start in November?\nThanks",
                "at": datetime(2026, 10, 12, 10, 15, tzinfo=timezone.utc).isoformat(),
                "data": {"from": "ana@acme.com", "from_name": "Ana Ruiz", "to": ["jeremy@agentmaple.com"]}}]
    out = em.quoted("Yes, November works.", history, "jeremy@agentmaple.com")
    head, quote = out["text"].split("\n\n", 1)
    assert head == "Yes, November works."
    assert quote.splitlines()[0].startswith("On ") and quote.splitlines()[0].endswith("Ana Ruiz <ana@acme.com> wrote:")
    assert quote.splitlines()[1:] == ["> Can we start in November?", "> Thanks"]
    assert 'class="gmail_quote"' in out["html"] and "Yes, November works." in out["html"]
    # Our own renderer folds the quote back behind ••• when the copy comes in.
    assert render.plain(render.html_body(out["html"])[0]) == "Yes, November works."


def test_older_messages_collapse_after_jeremys_last_reply():
    from universal_inbox.tui import default_open
    msgs = [{"who": w} for w in ("Ana", "You", "Ana", "You", "Ana", "Bo")]
    assert default_open(msgs) == {4, 5}
    assert default_open([{"who": "You"}]) == {0}
    assert default_open([{"who": "Ana"}, {"who": "Ana"}]) == {0, 1}


def test_a_bracketed_url_renders_as_markup_textual_reads():
    from textual.content import Content
    markup = render.text_markup("Logan\n[https://ci3.googleusercontent.com/mail-sig/AIorK4w]\nthanks")
    assert Content.from_markup(markup).plain == "Logan\n[↗ ci3.googleusercontent.com]\nthanks"


def test_a_bracketed_html_link_renders_as_markup_textual_reads():
    from textual.content import Content
    shown, _ = render.html_body('<p>Recording: [<a href="https://drive.google.com/file/d/1">Click Here</a>]</p>')
    assert Content.from_markup(shown).plain.startswith("Recording: [Click Here")

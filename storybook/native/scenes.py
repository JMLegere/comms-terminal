"""Synthetic review states only. No product persistence or provider behavior lives here."""

FROM = "jeremy@inbox.example"
EMAIL = "maya@northline.example"
PHONE = "+12025550124"
WORK_PHONE = "+12025550100"
SUBJECT = "Site walk next week"
BODY = "Hi Maya, can we arrange a site walk next week? Tuesday morning works on my side."
EMAIL_VALUES = dict(connection="Email · reviewed route", sender=FROM, to=EMAIL, cc="", subject=SUBJECT, body=BODY)
SMS_VALUES = dict(connection="Quo · Work", sender=WORK_PHONE, to=PHONE, body=BODY)
EDITED = dict(EMAIL_VALUES, cc="ravi@northline.example", subject="Site walk · Tuesday at 11",
              body="Hi Maya, I edited the plan: Tuesday at 11 works for the site walk. Ravi can join us.")

# Each name is explicit: an unknown Storybook state must fail, never silently fall back.
COMPOSE = {
    "BlankEmail": dict(values=dict(connection="", sender="", to="", cc="", subject="", body=""),
                       note="New email · no Thread selected. Choose Connection and exact sender; partial drafts can be saved."),
    "BlankSms": dict(sms=True, values=dict(connection="", sender="", to="", body=""),
                     note="New SMS · exactly one international phone. Work sender, consent and market clearance required."),
    "EmailReady": dict(values=dict(EMAIL_VALUES, cc="ravi@northline.example"), revision=3, saved=True, reviewed=True,
                       note="Revision 3 reviewed · exact sender, To, Cc, subject and words confirmed. Ready to submit."),
    "EmailWithoutSubject": dict(values=dict(EMAIL_VALUES, cc="ravi@northline.example", subject=""), revision=3, saved=True,
                                note="Subject explicitly empty · revision 3 saved. Review exact envelope; no inherited subject or Re:."),
    "SmsReady": dict(sms=True, values=SMS_VALUES, revision=2, saved=True, reviewed=True,
                     note="Revision 2 reviewed · one exact destination. Work sender, recipient consent and market clearance established."),
    "PartialDraft": dict(values=dict(EMAIL_VALUES, subject="", body="Hi Maya, can we arrange a site walk"),
                         note="Partial draft · subject unfinished. Save available; Send blocked until saved, reviewed and admitted."),
    "EditedAfterReview": dict(values=dict(EMAIL_VALUES, body="Hi Maya, can we arrange a site walk next week? Wednesday afternoon now works on my side."),
                              revision=4, note="Revision 4 unsaved · revision 3 review invalid. Save and review these new words."),
    "ResumeAfterRestart": dict(values=EDITED, revision=3, saved=True, drafts=True, title="Resume draft · D-104",
                               note="Restart · saved revision 3 restored exactly: Connection, From, To, Cc, subject and edited body. No send."),
    "SavingDraft": dict(values=EDITED, revision=4, drafts=True, frozen=True,
                        note="Saving draft… revision 4 pending. No saved-success claim until result exists.", action="Saving…"),
    "SaveFailed": dict(values=dict(EDITED, body="Hi Maya, newest unsaved edit: Wednesday at 14 now works. Please keep this text."),
                       revision=4, drafts=True, error=True,
                       note="Save failed · stored revision 3 unchanged. Latest revision 4 edits retained here; not saved.", action="Retry save"),
    "StaleDraftRevision": dict(values=dict(EDITED, body="Hi Maya, this older editor proposes Thursday at 10."),
                               revision=4, drafts=True, error=True,
                               note="Save conflict · editing revision 4, stored revision 5. Typed text retained; no overwrite.",
                               stored=dict(EDITED, body="Hi Maya, newer saved revision: Friday at 9 works for our site walk."), stored_revision=5),
}

ADMISSION = {
    "SenderNotSelected": dict(values=dict(EMAIL_VALUES, connection="", sender=""),
                              note="Sender not selected. Choose exact eligible Connection and sending identity. No implicit sender."),
    "ConsentMissing": dict(sms=True, values=SMS_VALUES,
                           note="SMS consent missing for this exact recipient. Contact membership and captured history are not permission."),
    "MarketNotCleared": dict(sms=True, values=SMS_VALUES,
                             note="SMS market clearance unproven for this US destination. Consent record alone does not clear route or market."),
    "UnsupportedConnection": dict(values=dict(EMAIL_VALUES, connection="Slack · reply-only", sender="Slack connection"),
                                  note="Unsupported Connection · existing replies only. No new email/SMS initiation or silent fallback."),
    "CredentialsUnavailable": dict(values=EMAIL_VALUES,
                                   note="Sending credentials unavailable to service. Operator must restore service-side access; no provider keys requested here."),
    "ServiceUnavailable": dict(values=EMAIL_VALUES,
                              note="Sending service unavailable before submission. No request dispatched; keep drafting while operator restores service."),
    "InvalidEmailHeader": dict(values=dict(EMAIL_VALUES, subject="Site walk next week\r\nCc: unexpected@northline.example"),
                               invalid_header=True, note="Invalid email header · Subject contains CR/LF controls. Correct header before review or send."),
    "InvalidSmsRecipient": dict(sms=True, values=dict(SMS_VALUES, to="+12025550124, +12025550125"),
                                note="Invalid SMS destination · two phones. Exactly one international phone required; no group or cold-SMS fan-out."),
    "StaleSendRevision": dict(values=EMAIL_VALUES, revision=5, reviewed=True,
                              note="Stale reviewed revision 5 refused before dispatch · service owns saved revision 6. Original fields retained; no substitution.",
                              stored=dict(EMAIL_VALUES, body="Hi Maya, saved revision 6 now proposes Wednesday at 14 for our site walk."), stored_revision=6),
}

RECIPIENTS = {
    "AmbiguousRecipient": dict(values=dict(EMAIL_VALUES, to="Maya Chen"), context="ambiguous",
                               note="Two name matches · no exact identity chosen. Display names never select or replace destinations."),
    "UnlinkedEmailWithHistory": dict(values=EMAIL_VALUES, context="history",
                                    note="Raw email · no linked Person. Exact captured incoming/outgoing history remains available."),
    "DetachedIdentityWithHistory": dict(values=EMAIL_VALUES, context="history", detached=True,
                                        note="Detached email Identity · former Person link removed. Exact captured history remains."),
    "RawPhoneWithHistory": dict(sms=True, values=SMS_VALUES, context="history",
                               note="Raw phone · no linked Person. Exact incoming/outgoing SMS history available; history is not consent."),
    "NoCapturedMatch": dict(values=dict(EMAIL_VALUES, to="lea@newsite.example"), context="empty",
                            note="No captured match for this exact identity. Successful empty local lookup does not mean no prior contact."),
    "ContextUnavailable": dict(values=EMAIL_VALUES, context="unavailable",
                               note="Captured local history unavailable. Prior contact unknown; envelope and typed body remain usable."),
    "LookupFailedPreservesDraft": dict(values=EMAIL_VALUES, context="failed", error=True,
                                       note="Captured-history lookup failed. Exact To, subject and typed body retained; Save still available."),
}

OUTCOMES = {
    "Submitting": dict(title="Submitting", note="Saved revision claimed. Waiting for acceptance evidence; editing and another submission locked."),
    "AcceptedEmail": dict(title="Accepted", accepted=True, note="Provider accepted reviewed email. Acceptance establishes neither delivery nor read."),
    "AcceptedSms": dict(title="Accepted", accepted=True, sms=True, note="Provider accepted reviewed SMS. Existing peer Channel C-17 reused; older T-082 remains Investigating."),
    "ProviderRejected": dict(title="Rejected", rejected=True, error=True,
                             note="Definitive provider rejection · recipient address refused. Exact draft retained; no sent Activity or new Thread."),
    "UnknownResponseLoss": dict(title="Outcome unknown · response lost",
                                note="Response lost after submission. May have been accepted. Retain exact attempt; do not resend."),
    "AcceptedRecordingPending": dict(title="Accepted · recording pending", pending=True,
                                     note="Provider acceptance confirmed. Inbox recording failed; intended Thread and Activity not recorded yet. No resend."),
    "DuplicateSubmissionBlocked": dict(title="Submission already in progress",
                                       note="Revision already claimed. Duplicate submission refused; original attempt remains in flight. No second dispatch."),
    "DuplicateAcceptedReceipt": dict(title="Already accepted", accepted=True,
                                     note="Duplicate submission returns original T-104 receipt. One dispatch, one outgoing Activity; no second send."),
    "DeliveryFailedReceipt": dict(title="Accepted · delivery failed", accepted=True, failed=True,
                                 note="Definitive later delivery failure correlated with accepted attempt. Acceptance and original Activity retained."),
    "DeliveryCoverageUnavailable": dict(title="Accepted · delivery unavailable", accepted=True,
                                       note="Route has no definitive failure signal. Unavailable coverage is not delivery success."),
    "InterruptedSubmission": dict(title="Outcome unknown · interrupted",
                                  note="Restart found interrupted revision 4 claim. Original attempt remains unknown; restart never dispatches again."),
    "EarlyEchoPending": dict(title="Outcome unknown · echo pending",
                             note="Own echo arrived before response. Held pending definitive attempt correlation; not inserted under older work. Body/time insufficient."),
    "RecoveredAccepted": dict(title="Accepted · recovered", accepted=True,
                              note="Definitively correlated original acceptance recovered. Intended T-104 recorded once; no resend or delivery/read claim."),
    "RecoveredRejected": dict(title="Rejected · recovered", rejected=True, error=True,
                              note="Definitive correlated evidence: original A-04 rejected. Exact envelope editable again; no accepted Thread or Activity."),
    "StillUnknown": dict(title="Outcome unknown · unresolved",
                         note="No definitive correlation. Similar body, time or unsupported echo cannot resolve original attempt. Retain frozen evidence; no resend."),
}

THREADS = {
    "NewEmailThread": dict(thread=104, note="T-104 · distinct new email work. Same recipient/subject as T-081; older work remains Investigating. No reply references or Re:."),
    "NewSmsThreadExistingChannel": dict(thread=105, sms=True,
                                       note="T-105 · distinct new SMS work in existing C-17. Older T-082 remains Investigating; no duplicate Channel."),
    "DeliveryFailedThread": dict(thread=104, failed=True,
                                 note="T-104 · accepted email retained. Later delivery failure does not add Activity or change Waiting status."),
    "SnoozedQueueNewThread": dict(thread=104, old_waiting=True,
                                 note="T-104 enters Snoozed / Waiting for them. T-082 still waiting unchanged; original five-day follow-up applies."),
    "IncomingReplyTodo": dict(thread=105, sms=True, detail="NEW_REPLY", incoming=True,
                              note="T-105 · attributed incoming reply wakes correct work to Todo. Original You Activity retained; T-082 unchanged."),
    "SmsReplyOutsideWindow": dict(thread=106, sms=True, detail="CREATED", incoming=True,
                                 note="T-106 · incoming SMS outside existing grouping window. Same C-17; earlier T-105 remains Waiting for them."),
    "IgnoredThreadReply": dict(thread=105, sms=True, detail="IGNORED", incoming=True,
                              note="T-105 · reply attributed to Ignored work stays Ignored. No automatic unignore; T-082 unchanged."),
    "FiveDayFollowUp": dict(thread=104, detail="NEW_REPLY", expired=True,
                            note="T-104 · five-day wait expired. Returned to Todo for follow-up; original Activity retained, no new message sent."),
    "ExistingReplyComparison": dict(thread=81, reply=True,
                                    note="T-081 · existing Reply, not new composition. Same Thread; history-derived To/Cc, Re: subject and original reply context."),
}

PEOPLE = {
    "OutgoingOnlyPersonHistory": dict(person=51, thread=104,
                                      note="Two canonical Threads · Todo 0 · Snoozed 2. Recipient association includes outgoing email/SMS; actors remain You / NULL identity."),
    "MixedIncomingOutgoingHistory": dict(person=51, thread=104, mixed=True,
                                        note="Three canonical Threads · Todo 1 · Snoozed 2. T-077 aliases canonical T-081, counted once. Incoming Maya; outgoing You."),
    "UnlinkedRecipientHistory": dict(thread=204, raw=True,
                                    note="Exact unlinked@northline.example · no linked Person. Phone +12025550125 detached. Two canonical Threads: Todo 1, Snoozed 1; no Person created."),
}

REFERENCES = {
    "reference-current-app--queue": dict(kind="reference", page="queue", state="Queue"),
    "reference-current-app--email-reply": dict(kind="reference", page="reply", state="EmailReply"),
    "reference-current-app--people": dict(kind="reference", page="people", state="People"),
}


def scene(name):
    for group, states in (("compose", COMPOSE), ("admission", ADMISSION), ("recipient", RECIPIENTS),
                          ("outcome", OUTCOMES), ("thread", THREADS), ("people", PEOPLE)):
        if name in states:
            value = dict(states[name], group=group, state=name)
            if group == "admission":
                value.update(error=True, saved=True)
            if group in ("compose", "admission", "recipient"):
                value.setdefault("revision", 1 if group == "compose" else 3)
                value["values"] = dict(value["values"])
            if group == "outcome":
                value.update(revision=4, values=dict(SMS_VALUES if value.get("sms") else EMAIL_VALUES),
                             frozen=not value.get("rejected"), reviewed=not value.get("rejected"), saved=True)
            return value
    if name in ("EmptyDrafts", "SavedAndUnresolvedDrafts"):
        return dict(group="drafts", state=name, populated=name == "SavedAndUnresolvedDrafts")
    raise ValueError(f"Unsupported native state: {name}")

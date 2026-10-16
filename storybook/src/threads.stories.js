import { nativeStory } from './snapshot.js';

export default { title: 'Changed views/Threads', parameters: { layout: 'fullscreen' } };

export const NewEmailThread = nativeStory('NewEmailThread', 'Explicit accepted new email remains distinct T-104 even with same recipient/Subject, one outgoing You Activity and Waiting for them. Older T-081 stays Investigating. No inherited Re:, references or quoted reply history.');
export const NewSmsThreadExistingChannel = nativeStory('NewSmsThreadExistingChannel', 'Explicit accepted SMS creates distinct Inbox T-105 in existing C-17. Older T-082/status untouched. Channel identity is not work identity; no duplicate Channel or changed incoming grouping.');
export const DeliveryFailedThread = nativeStory('DeliveryFailedThread', 'Correlated delayed delivery failure is separate evidence, not a fake Activity. Original accepted You message remains sole Activity; Waiting for them unchanged. No rollback, resend or automatic triage.');
export const SnoozedQueueNewThread = nativeStory('SnoozedQueueNewThread', 'Accepted outbound work appears in existing Snoozed queue, Waiting for them with five-day follow-up. Other waiting work and existing status/Case semantics remain unchanged.');
export const IncomingReplyTodo = nativeStory('IncomingReplyTodo', 'Attributed incoming SMS uses existing latest-activity/time-window grouping and wakes correct non-Ignored outbound Thread to Needs next response. Older work unchanged; no invented grouping duration or exact-topic guarantee.');
export const SmsReplyOutsideWindow = nativeStory('SmsReplyOutsideWindow', 'Incoming SMS outside existing grouping window starts distinct incoming T-106 / Needs first response in same C-17. Accepted T-105 remains Waiting for them. No always-latest-initiation rule.');
export const IgnoredThreadReply = nativeStory('IgnoredThreadReply', 'Existing regression contract: attributed reply to Ignored Thread stays Ignored. Captured Activity may appear without automatic unignore; initiation does not change that rule.');
export const FiveDayFollowUp = nativeStory('FiveDayFollowUp', 'Existing five-day wake rule returns accepted work to Todo / Needs next response. Original outgoing Activity remains; timer creates neither message nor resend.');
export const ExistingReplyComparison = nativeStory('ExistingReplyComparison', 'Existing Reply stays in original T-081 with history-derived To/Cc, Re: Subject and reply context/references. It is not new initiation; legacy in-memory inline draft gains no silent persistence upgrade.');

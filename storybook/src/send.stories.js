import { nativeStory } from './snapshot.js';

export default { title: 'New thread/Send outcomes', parameters: { layout: 'fullscreen' } };

export const Submitting = nativeStory('Submitting', 'Claimed saved revision freezes exact sender, destination, envelope and body while acceptance evidence is pending. Send locked; no speculative accepted Thread or sent Activity.');
export const AcceptedEmail = nativeStory('AcceptedEmail', 'Stable synthetic email acceptance records one outgoing You Activity in distinct new Thread T-104, Snoozed / Waiting for them with five-day follow-up. Same-recipient older work unchanged. Acceptance is not delivery or read.');
export const AcceptedSms = nativeStory('AcceptedSms', 'Stable synthetic one-to-one SMS acceptance creates new Inbox Thread T-105 in existing peer Channel C-17, one You Activity, Waiting for them and five-day follow-up. Older Investigating Thread unchanged; no duplicate Channel or live eligibility claim.');
export const ProviderRejected = nativeStory('ProviderRejected', 'Definitive rejection retains editable exact draft and original rejected-attempt evidence. Zero sent Activities and no accepted Thread. Any new eligible attempt requires separate saved-revision review.');
export const UnknownResponseLoss = nativeStory('UnknownResponseLoss', 'Response lost after submission means unknown, never Not sent. Exact reviewed envelope and original attempt remain frozen. Insufficient evidence grants no resend.');
export const AcceptedRecordingPending = nativeStory('AcceptedRecordingPending', 'Provider acceptance is confirmed but local Inbox recording remains incomplete. No completed new Thread/Activity claim. Original envelope is frozen; definitive recovery must record once without another dispatch.');
export const DuplicateSubmissionBlocked = nativeStory('DuplicateSubmissionBlocked', 'Duplicate claim on same in-flight saved revision is refused. Original envelope/attempt remain frozen; no second dispatch or speculative Activity.');
export const DuplicateAcceptedReceipt = nativeStory('DuplicateAcceptedReceipt', 'Duplicate accepted receipt refers to existing T-104 and its one outgoing Activity. Original receipt reused; no second Thread or dispatch.');
export const DeliveryFailedReceipt = nativeStory('DeliveryFailedReceipt', 'Definitively correlated delivery failure arrives after acceptance. Original acceptance, You Activity and Waiting for them remain; separate failure evidence does not roll back, resend or automatically change status.');
export const DeliveryCoverageUnavailable = nativeStory('DeliveryCoverageUnavailable', 'Unsupported failure signal means unavailable coverage, not delivery success. Original accepted Thread/Activity and ordinary follow-up remain; no invented polling, delivered status or read claim.');

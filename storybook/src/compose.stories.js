import { nativeStory } from './snapshot.js';

export default { title: 'New thread/Compose' };

export const BlankEmail = nativeStory('BlankEmail', 'Independent New Thread email begins with blank From, exact To, optional Cc, editable Subject and plain body. No inherited Re:, reply references or quoted history. Unresolved sender and admission block Send.');
export const EmailReady = nativeStory('EmailReady', 'Synthetic revision 3 has one saved, reviewed email envelope with optional Cc. Explicit initiation stays standalone for an existing recipient. Any field edit invalidates review; this state claims neither provider acceptance nor a new Thread.');
export const EmailWithoutSubject = nativeStory('EmailWithoutSubject', 'Subject is an explicit empty string, not omitted or inherited. Exact envelope still requires saved-revision review and service-owned admission; no Re: or quoted reply history.');
export const BlankSms = nativeStory('BlankSms', 'Independent SMS draft has one exact international phone destination and no Cc or Subject. Existing contact proves neither consent nor market clearance; Send remains blocked.');
export const SmsReady = nativeStory('SmsReady', 'Synthetic saved, reviewed SMS revision 2 has one phone and no Cc or Subject. Fixture supplies synthetic Work-number consent and clearance only. No acceptance is claimed; eventual acceptance would create distinct Inbox work in the existing peer Channel.');
export const PartialDraft = nativeStory('PartialDraft', 'Incomplete email envelope and unfinished body remain recoverable as draft work without sending. Subject is unfinished; no sent Activity exists. Durable restart-safe storage is a product contract, not evidence supplied by this capture.');
export const EditedAfterReview = nativeStory('EditedAfterReview', 'Revision 4 retains edits made after revision 3 review. Previous review is invalid; exact new words require save and review before any eligible send. No old reviewed envelope may be silently replaced.');

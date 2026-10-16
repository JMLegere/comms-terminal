import { nativeStory } from './snapshot.js';

export default { title: 'New thread/Recipient context' };

export const AmbiguousRecipient = nativeStory('AmbiguousRecipient', 'Two matching display names require explicit exact-identity choice. Candidate emails stay verbatim; no silent destination substitution, merge or default choice. Send remains blocked.');
export const UnlinkedEmailWithHistory = nativeStory('UnlinkedEmailWithHistory', 'Exact raw email finds captured incoming and outgoing history without a linked Person. Outgoing actor remains You; older Thread/status and new composition intent remain unchanged. External Attio coverage is explicitly unavailable.');
export const DetachedIdentityWithHistory = nativeStory('DetachedIdentityWithHistory', 'Removing Person linkage does not erase exact-destination captured history. Existing inbound/outbound Threads remain read-only context; no Person/CRM write or identity substitution.');
export const RawPhoneWithHistory = nativeStory('RawPhoneWithHistory', 'Exact international raw phone finds captured SMS history without Person linkage. One To, no Cc or Subject; outgoing actor remains You. History proves neither consent nor permission and does not turn composition into Reply.');
export const NoCapturedMatch = nativeStory('NoCapturedMatch', 'Successful synthetic local lookup has no captured match for exact raw email. Unavailable external coverage prevents any no-prior-contact claim. No automatic Person creation or replacement destination.');
export const ContextUnavailable = nativeStory('ContextUnavailable', 'Unavailable local captured history and unavailable external Attio coverage are distinct from successful empty lookup. Exact envelope/body remain draft work; prior contact remains unknown.');
export const LookupFailedPreservesDraft = nativeStory('LookupFailedPreservesDraft', 'Visible captured-history lookup failure preserves exact To, Subject and typed body. Draft work remains recoverable. Failure does not imply an empty result or a successful external read.');

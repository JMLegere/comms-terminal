import { nativeStory } from './snapshot.js';

export default { title: 'New thread/Admission' };

export const SenderNotSelected = nativeStory('SenderNotSelected', 'No default or implicit sender is admitted. Exact destination, Subject and body stay recoverable; selecting a synthetic sender alone cannot authorize a real route. Zero dispatch and no sent Activity.');
export const ConsentMissing = nativeStory('ConsentMissing', 'Existing contact does not prove SMS consent. Synthetic Work-number draft is retained with zero sends; no consent waiver, enrollment or contact substitution. Service owns admission.');
export const MarketNotCleared = nativeStory('MarketNotCleared', 'Even with synthetic consent, market/route clearance remains unproven. Draft retained and zero requests. This state is not legal or provider clearance.');
export const UnsupportedConnection = nativeStory('UnsupportedConnection', 'Reply capability does not imply initiation capability. Selected incompatible Slack Connection is denied before provider request; envelope/body remain draft work. No Slack initiation mode or silent route fallback.');
export const CredentialsUnavailable = nativeStory('CredentialsUnavailable', 'Provider credentials stay service-side and are never requested or shown by composer. Unavailable credentials retain exact draft with zero sends and no account-access change.');
export const ServiceUnavailable = nativeStory('ServiceUnavailable', 'Service unavailable before submission proves zero dispatch, unlike an unknown post-submission outcome. Draft retained; no new Thread, Waiting state or sent Activity.');
export const InvalidEmailHeader = nativeStory('InvalidEmailHeader', 'Corrupt synthetic Subject exposes an injected second header line. Strict header validation denies dispatch while retaining invalid/partial draft. Normal Subject is single-line; corrected text still requires service-owned admission.');
export const InvalidSmsRecipient = nativeStory('InvalidSmsRecipient', 'Multiple SMS destinations are denied before dispatch. Exact invalid recipient/body remain draft work; no Cc, Subject or group fan-out. One corrected phone still needs independent consent and clearance.');
export const StaleSendRevision = nativeStory('StaleSendRevision', 'Reviewed revision 5 is stale against stored revision 6. No provider request, overwrite or silent envelope/body replacement. Explicit compare/reload would not itself grant Send permission.');

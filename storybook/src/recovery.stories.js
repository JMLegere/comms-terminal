import { nativeStory } from './snapshot.js';

export default { title: 'New thread/Recovery', parameters: { layout: 'fullscreen' } };

export const InterruptedSubmission = nativeStory('InterruptedSubmission', 'Interrupted claimed revision 4 is unknown after restart. Exact sender/destination/Subject/body and original attempt survive; no fresh dispatch. Definitive correlated evidence is required to resolve the attempt.');
export const EarlyEchoPending = nativeStory('EarlyEchoPending', 'Own-message echo precedes send response and is held for definitive attempt correlation. It is not appended to older work; body/time resemblance alone cannot resolve acceptance or permit resend.');
export const RecoveredAccepted = nativeStory('RecoveredAccepted', 'Definitive synthetic acceptance resolves original revision 4 and records intended T-104 once: one outgoing You Activity, Waiting for them, five-day follow-up. Envelope stays frozen; recovery is not resend or delivery/read proof.');
export const RecoveredRejected = nativeStory('RecoveredRejected', 'Definitively correlated rejection restores original exact envelope as retained editable draft with original attempt evidence. No accepted Thread/Activity or Waiting state; separate eligible-route review required for any later attempt.');
export const StillUnknown = nativeStory('StillUnknown', 'Unsupported correlation or body/time resemblance leaves original attempt unknown. Exact destination/revision/body and evidence stay frozen. No blind resend, automatic replay or Not sent claim.');

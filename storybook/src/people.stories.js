import { nativeStory } from './snapshot.js';

export default { title: 'Changed views/People', parameters: { layout: 'fullscreen' } };

export const OutgoingOnlyPersonHistory = nativeStory('OutgoingOnlyPersonHistory', 'Proposed exact-recipient association exposes known Person outgoing-only email/SMS history: two canonical Threads, zero Todo, two Snoozed. Outgoing actors remain You / NULL identity; no recipient-as-actor rewrite or CRM operation.');
export const UnlinkedRecipientHistory = nativeStory('UnlinkedRecipientHistory', 'Exact raw email or detached phone finds captured inbound/outbound history without linked Person. Destination stays raw and outgoing actor You. External history unavailable does not mean no prior contact; no auto-create, merge or substitution.');
export const MixedIncomingOutgoingHistory = nativeStory('MixedIncomingOutgoingHistory', 'Recipient association joins captured incoming and outgoing work while retaining incoming actors and outgoing You. Three canonical Threads: one Todo, two Snoozed. Alias T-077 points to T-081 and counts once; mappings unchanged, external coverage unavailable.');

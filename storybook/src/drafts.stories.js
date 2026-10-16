import { nativeStory } from './snapshot.js';

export default { title: 'New thread/Drafts' };

export const EmptyDrafts = nativeStory('EmptyDrafts', 'Empty synthetic saved-draft list is distinct from Inbox work. New composition starts independently of Thread selection. Saved drafts are unsent work, never sent Activities.');
export const SavedAndUnresolvedDrafts = nativeStory('SavedAndUnresolvedDrafts', 'Editable email and partial SMS drafts are separate from frozen unknown and accepted-but-unrecorded receipts. Unknown does not mean Not sent; accepted-pending does not claim completed local Thread/Activity recording.');
export const ResumeAfterRestart = nativeStory('ResumeAfterRestart', 'Synthetic resumed saved snapshot restores exact From, To, Cc, changed Subject and edited body. No sending or Activity occurs. Capture shows restart-resume contract, not a real restart or persisted write.');
export const SavingDraft = nativeStory('SavingDraft', 'Atomic-save result is pending. Inputs, Save, Review, Send and Close remain disabled; no implicit timer or submission resolves this fixed state.');
export const SaveFailed = nativeStory('SaveFailed', 'Visible save failure retains latest unsaved edits without claiming persistence. Recovering save service alone would not save them; explicit save remains required. No sent Activity or eligible Send is claimed.');
export const StaleDraftRevision = nativeStory('StaleDraftRevision', 'Expected-revision conflict refuses overwrite of stored revision 5. Current typed revision 4 stays visible with Send unavailable; compare/reload would require explicit choice rather than silent merge or replacement.');

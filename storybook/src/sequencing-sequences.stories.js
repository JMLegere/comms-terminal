import { nativeStory } from './snapshot.js';

export default { title: 'Sequencing/Sequences' };

export const SequenceEditor = nativeStory('SequenceEditor', "A Sequence is an ordered collection of Step rows. The selected day-9 email Step replies to Step 1; its detail pane edits kind, gap, time, reply-to and body, previews the text for Sam Lee, and states that edits apply to Steps not yet due.");
export const SequenceEditorWarnings = nativeStory('SequenceEditorWarnings', "Validator warnings: an unknown {title} fill-in and a first Step set to reply. Both rows are flagged and the detail pane names each problem and its fix.");
export const SequenceArchive = nativeStory('SequenceArchive', "A used Sequence can only be archived. The archive menu asks whether to cancel the live Enrollments (Finished: Canceled) or let them finish; archived Sequences take no new Enrollments and can be restored.");
export const SequenceStepDeleted = nativeStory('SequenceStepDeleted', "Deleting a Step is live: the toast says how many Enrollments moved on and how to undo.");

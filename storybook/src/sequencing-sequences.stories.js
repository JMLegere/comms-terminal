import { nativeStory } from './snapshot.js';

export default { title: 'Sequencing/Sequences' };

export const SequencesList = nativeStory('SequencesList', "Sequences in the rail opens two stacked panes: every Sequence on top, the selected Sequence's Steps below. j/k on the top pane changes which Sequence's Steps show; the side pane summarises it. enter moves to its Steps, n makes a new Sequence, a archives; archived Sequences fold under Archived.");
export const SequenceNew = nativeStory('SequenceNew', "n on the Sequences pane asks for a name; the new Sequence joins the list and its empty Steps pane takes focus. It sends from the only email Connection; n adds the first Step, esc returns to the Sequences pane.");
export const SequenceEditor = nativeStory('SequenceEditor', "enter on Secret Sequence moves focus to its Steps pane, under the Sequences pane (esc goes back up). A Sequence is an ordered collection of Step rows. The selected day-9 email Step replies to Step 1; its detail pane edits kind, gap, time, reply-to and body, previews the text for Sam Lee, and states that edits apply to Tasks not yet due.");
export const SequenceEditorWarnings = nativeStory('SequenceEditorWarnings', "Validator warnings: an unknown {title} fill-in and a first Step set to reply. Both rows are flagged and the detail pane names each problem and its fix.");
export const SequenceArchive = nativeStory('SequenceArchive', "A used Sequence can only be archived. The archive menu asks whether to cancel the live Enrollments (Finished: Canceled) or let them finish; archived Sequences take no new Enrollments and can be restored.");
export const SequenceStepDeleted = nativeStory('SequenceStepDeleted', "Deleting a Step is live: the toast says how many Enrollments moved on and how to undo.");

import { nativeStory } from './snapshot.js';

export default { title: 'Sequencing/Tasks' };

export const TasksEmailDue = nativeStory('TasksEmailDue', "Tasks page grouped Overdue, Today, Later today. The selected day-1 email Task opens its real Draft in the Task pane, with Task and Enrollment properties above the Draft's own and Attio and Tana context below. ctrl+enter sends; sending completes the Task and the next Step's Task becomes due by its gap.");
export const TasksCallBlock = nativeStory('TasksCallBlock', "Filtered to Call Tasks (f, then Call). An overdue call Task shows its age; the Task pane carries the number, the two-dial rule, the script and voicemail text. Jeremy dials in the Quo app; e marks it done and moves to the next row.");
export const TasksMissingFillIn = nativeStory('TasksMissingFillIn', "Priya Shah has no Organization, so {company} stays in the Draft as a marker. The row is flagged and the Task pane states that sending is refused until the marker is replaced.");
export const TasksReplyTargetSkipped = nativeStory('TasksReplyTargetSkipped', "Day 9 replies in the day-1 Thread, but day 1 was skipped. The Task stays due with a warning and no Draft until Jeremy chooses: n start a new Thread, or x skip.");
export const TasksLinkedIn = nativeStory('TasksLinkedIn', "LinkedIn Task: the action and the note text with fill-ins applied, ready to paste. Done is manual (e); LinkedIn replies are invisible to Inbox, so a reply there means pausing by hand on the Person page.");
export const TasksFilter = nativeStory('TasksFilter', "f opens one filter menu: all Tasks, one kind, or one Sequence. Each row names its Sequence, so Tasks from several Sequences share the list.");
export const TasksPausedToast = nativeStory('TasksPausedToast', "Dana Roy replied, so her Enrollment paused: her overdue call Task left the list and the toast states the consequence and the way back.");
export const TasksEmpty = nativeStory('TasksEmpty', "No Due Tasks: the empty state says what empty means and where Tasks come from.");

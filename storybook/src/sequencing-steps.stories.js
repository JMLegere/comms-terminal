import { nativeStory } from './snapshot.js';

export default { title: 'Sequencing/Steps' };

export const StepsEmailDue = nativeStory('StepsEmailDue', "Steps page grouped Overdue, Today, Later today. The selected day-1 email Step opens its real Draft in the Step pane, with Step and Enrollment properties above the Draft's own and Attio and Tana context below. ctrl+enter sends; sending completes the Step and the next Step becomes due by its gap.");
export const StepsCallBlock = nativeStory('StepsCallBlock', "Kind filter on Call (f). An overdue call Step shows its age; the Step pane carries the number, the two-dial rule, the script and voicemail text. Jeremy dials in the Quo app; e marks it done and moves to the next row.");
export const StepsMissingFillIn = nativeStory('StepsMissingFillIn', "Priya Shah has no Organization, so {company} stays in the Draft as a marker. The row is flagged and the Step pane states that sending is refused until the marker is replaced.");
export const StepsReplyTargetSkipped = nativeStory('StepsReplyTargetSkipped', "Day 9 replies in the day-1 Thread, but day 1 was skipped. The Step stays due with a warning and no Draft until Jeremy chooses: n start a new Thread, or x skip.");
export const StepsLinkedIn = nativeStory('StepsLinkedIn', "LinkedIn Step: the action and the note text with fill-ins applied, ready to paste. Done is manual (e); LinkedIn replies are invisible to Inbox, so a reply there means pausing by hand on the Person page.");
export const StepsPausedToast = nativeStory('StepsPausedToast', "Dana Roy replied, so her Enrollment paused: her overdue call Step left the list and the toast states the consequence and the way back.");
export const StepsEmpty = nativeStory('StepsEmpty', "No Due Steps: the empty state says what empty means and where Steps come from.");

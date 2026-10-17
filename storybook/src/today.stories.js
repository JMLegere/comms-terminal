import { nativeStory } from './snapshot.js';

export default { title: 'Today' };

export const TodayLists = nativeStory('TodayLists', "The console opens on Today, the rail's first line with the count of what it lists. Todo Threads (grouped by Case) take the room the Due Tasks and Drafts lists leave; the Tasks title counts the overdue in red. Drafts list failed sends first, then unsent, then scheduled by send time; a Task's Draft shows only through its Task. The statusline reads TODAY › THREADS with the Thread keys.");
export const TodayTasks = nativeStory('TodayTasks', "↓ past the last Thread runs on into Due Tasks: the overdue call Task fills the right pane and the statusline reads TODAY › TASKS with the Task keys (e done · next, x skip · next, / filter).");
export const TodayAllClear = nativeStory('TodayAllClear', "Nothing waiting: every list hides and All clear says when the next snoozed Thread wakes.");

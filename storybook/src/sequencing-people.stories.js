import { nativeStory } from './snapshot.js';

export default { title: 'Sequencing/People' };

export const PersonSequencePaused = nativeStory('PersonSequencePaused', "The Person page's Sequence section, one block per Enrollment: Dana is in two Sequences, and her reply paused both. Each block shows state, current day, next Task, and the Tasks timeline. r resumes and f finishes (Completed, Unsubscribed or Canceled); with two Enrollments, each asks which.");
export const EnrollFromPeople = nativeStory('EnrollFromPeople', "Enroll one Person at a time from a People row (s). The menu lists every active Sequence; one the Person is already in is marked and refused. The first Task is due today.");
export const EnrollRefusedUnsubscribed = nativeStory('EnrollRefusedUnsubscribed', "Enrolling an Unsubscribed Person is refused, and the toast names the reason.");

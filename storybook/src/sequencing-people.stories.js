import { nativeStory } from './snapshot.js';

export default { title: 'Sequencing/People' };

export const PersonSequencePaused = nativeStory('PersonSequencePaused', "The Person page's Sequence section: state Paused (Dana replied), current day, next Step, and the Steps timeline. r resumes (the next Step is due immediately); f finishes as Completed, Unsubscribed or Canceled.");
export const EnrollFromPeople = nativeStory('EnrollFromPeople', "Enroll one Person at a time from a People row (s): pick a Sequence; the first Step is due today.");
export const EnrollRefusedUnsubscribed = nativeStory('EnrollRefusedUnsubscribed', "Enrolling an Unsubscribed Person is refused, and the toast names the reason.");

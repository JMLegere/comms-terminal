import '../src/styles.css';

export default {
  parameters: {
    layout: 'fullscreen',
    options: {
      storySort: {
        order: [
          'Reference current app',
          'New thread',
          ['Compose', 'Drafts', 'Recipient context', 'Admission', 'Send outcomes', 'Recovery'],
          'Changed views',
          ['Threads', 'People'],
          'Sequencing',
          ['Steps', 'Sequences', 'People'],
        ],
      },
    },
  },
};

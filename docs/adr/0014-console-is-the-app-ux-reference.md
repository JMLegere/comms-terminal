# The Console is the App's reference for how work looks and behaves

Status: accepted (2026-11-02). Plan: `.agents/prd/2026-11-02-app-console-conformance.md`. Discovery: `.agents/discovery/2026-10-31-desktop-app.md` (feedback cycle, Phase 4 approved 2026-11-02).

Jeremy compared every App screen with the Console and chose for each of 151 differences: 114 rejected, 37 accepted (L0 and 36 D-items). He cannot move his daily work from the Console to the App until the App is better. The review showed that the App's native gains (readable HTML, inline images, one-key link opening, file drop) came bundled with changes to rows, navigation, wording, targets and saved work that he does not accept.

The App therefore follows the running Console:

- **Reference.** For anything the Console does, the App does the same: the same information, the same page stack and navigation, the same keys, the same wording, the same command targets, the same save and undo behaviour. "The same" is judged against `tui.py` and `docs/design/console.md`, not against a description of them.
- **Accepted differences.** The differences Jeremy accepted are listed in `docs/design/app.md` §6. They are exceptions, each with its review ID. Anything not on that list conforms.
- **Safety is not an accepted difference.** An accepted difference keeps its layout or wording, but never lets a command act on an object other than the one shown, and never makes a locked Draft editable. D87 and D74 were accepted with exactly those defects; the defects are fixed and the rest of each difference stays.
- **New differences.** A change that makes the App differ from the Console needs Jeremy's explicit approval, recorded in `docs/design/app.md` §6 with its reason. An agent editing Interface files may restyle the App; it does not add an unapproved difference to the shipped defaults.
- **Same architecture.** This decision changes App presentation only. The Local core, the Local API's grow-only contract, the Service as the only writer, Origins, and the Console's own copy (ADR 0013) are unchanged. No shared presentation layer is added: the Console and the App each keep their own presentation code.

## Considered Options

- **Copy the Console wholesale**: one rule, but it reverses the 37 accepted differences, including the native reading and file gains that motivated the App.
- **Native first, Console as inspiration**: the App as built; Jeremy rejected 114 of its differences and cannot migrate.
- **A shared presentation model both Clients project**: centralises policy, but adds a cross-client model, mapping and synchronisation that the approved discovery scope excludes. Revisit only if the two Clients repeatedly drift after this conformance work.

## Consequences

- Presentation decisions are written twice, once per Client. Drift is caught by tests: `tests/test_app_parity.py` (keys, and Console strings where they can be compared) and one scenario per conformed behaviour in `features/app.feature`.
- Some Console behaviours exist because of terminal limits. Where Jeremy chose a native equivalent, it is an accepted difference: `ctrl+o` opens the system file picker, and links in message bodies open with Ctrl+click instead of Ctrl+Shift+click.
- The App's dark theme and its list filters, header folding, rail Cases and delete confirmations are removed, because Jeremy rejected them.
- Jeremy's one-week App-default trial starts after this work merges; until then the Console stays his working tool.

import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { references } from './src/snapshot.js';

const required = {
  compose: ['BlankEmail', 'EmailReady', 'EmailWithoutSubject', 'BlankSms', 'SmsReady', 'PartialDraft', 'EditedAfterReview'],
  drafts: ['EmptyDrafts', 'SavedAndUnresolvedDrafts', 'ResumeAfterRestart', 'SavingDraft', 'SaveFailed', 'StaleDraftRevision'],
  recipients: ['AmbiguousRecipient', 'UnlinkedEmailWithHistory', 'DetachedIdentityWithHistory', 'RawPhoneWithHistory', 'NoCapturedMatch', 'ContextUnavailable', 'LookupFailedPreservesDraft'],
  admission: ['SenderNotSelected', 'ConsentMissing', 'MarketNotCleared', 'UnsupportedConnection', 'CredentialsUnavailable', 'ServiceUnavailable', 'InvalidEmailHeader', 'InvalidSmsRecipient', 'StaleSendRevision'],
  send: ['Submitting', 'AcceptedEmail', 'AcceptedSms', 'ProviderRejected', 'UnknownResponseLoss', 'AcceptedRecordingPending', 'DuplicateSubmissionBlocked', 'DuplicateAcceptedReceipt', 'DeliveryFailedReceipt', 'DeliveryCoverageUnavailable'],
  recovery: ['InterruptedSubmission', 'EarlyEchoPending', 'RecoveredAccepted', 'RecoveredRejected', 'StillUnknown'],
  threads: ['NewEmailThread', 'NewSmsThreadExistingChannel', 'DeliveryFailedThread', 'SnoozedQueueNewThread', 'IncomingReplyTodo', 'SmsReplyOutsideWindow', 'IgnoredThreadReply', 'FiveDayFollowUp', 'ExistingReplyComparison'],
  people: ['OutgoingOnlyPersonHistory', 'UnlinkedRecipientHistory', 'MixedIncomingOutgoingHistory'],
};
const local = path => new URL(path, import.meta.url);
const text = path => readFile(local(path), 'utf8');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const slug = value => value.replace(/([a-z0-9])([A-Z])/g, '$1-$2').replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toLowerCase();
const catalog = JSON.parse(await text('./scene-catalog.json'));
// Sequencing P0 mocks (.agents/prd/2026-10-16-sequencing.md) live beside the 56 composition states.
const sequencingFiles = { 'sequencing-tasks': 'Sequencing/Tasks', 'sequencing-sequences': 'Sequencing/Sequences', 'sequencing-people': 'Sequencing/People' };
const sequencing = catalog.filter(scene => scene.file.startsWith('sequencing-'));
// Today (.agents/prd/2026-10-17-today-view.md) is captured from the real page, beside the mocks.
const todayFiles = { today: 'Today' };
const today = catalog.filter(scene => scene.file === 'today.stories.js');
const composition = catalog.filter(scene => !scene.file.startsWith('sequencing-') && scene.file !== 'today.stories.js');
assert.equal(composition.length, 56, 'Expected 56 preserved mock states');
assert.equal(new Set(catalog.map(scene => scene.id)).size, catalog.length, 'Duplicate catalog IDs');
assert.equal(sequencing.length, 17, 'Expected 17 Sequencing mock states');
assert.equal(today.length, 3, 'Expected 3 Today states');
const requirements = new Set();
const scenarios = new Set();
let count = 0;
for (const [file, names] of Object.entries(required)) {
  // Import without a DOM: capture renderers must defer all browser work until render().
  const stories = await import(`./src/${file}.stories.js`);
  assert.deepEqual(Object.keys(stories).filter(name => name !== 'default').sort(), [...names].sort(), `${file}: named states changed`);
  const source = await text(`./src/${file}.stories.js`);
  assert.match(source, /^import \{ nativeStory \} from '\.\/snapshot\.js';/);
  assert(!/document\.|innerHTML|<(?:form|input|textarea|table|main|nav)\b/.test(source), `${file}: independent HTML app recreation`);
  for (const name of names) {
    const scene = catalog.find(entry => entry.file === `${file}.stories.js` && entry.exportName === name);
    assert(scene, `${file}/${name}: missing catalog state`);
    assert.equal(stories.default.title, scene.title, `${file}: group title changed`);
    assert.equal(`${slug(scene.title)}--${slug(name)}`, scene.id, `${file}/${name}: Storybook ID changed`);
    const story = stories[name];
    assert.equal(typeof story.render, 'function', `${file}/${name}: missing capture renderer`);
    assert.deepEqual(story.parameters.nativeCapture, { id: scene.id, kind: 'mock', columns: [187, 80] });
    const prd = story.parameters.prd;
    assert.deepEqual(prd, scene.parameters.prd, `${file}/${name}: requirement/scenario/change metadata changed`);
    assert(prd.requirements.length && prd.scenarios.length, `${file}/${name}: missing PRD coverage`);
    assert(['new', 'changed'].includes(prd.change), `${file}/${name}: missing change classification`);
    const description = story.parameters.docs.description.story;
    assert.match(description, /Behavioral contract \(synthetic projection\)/);
    assert.match(description, /Static capture/);
    assert.match(description, /Canvas controls do not work/);
    for (const id of prd.requirements) requirements.add(id);
    for (const id of prd.scenarios) scenarios.add(id);
    count++;
  }
}
assert.equal(count, 56);
for (let n = 17; n <= 29; n++) assert(requirements.has(`FR-${n}`), `FR-${n}: not covered`);
const sequencingRequirements = new Set();
for (const [file, title] of Object.entries(sequencingFiles)) {
  const stories = await import(`./src/${file}.stories.js`);
  const names = sequencing.filter(scene => scene.file === `${file}.stories.js`).map(scene => scene.exportName);
  assert.equal(stories.default.title, title, `${file}: group title changed`);
  assert.deepEqual(Object.keys(stories).filter(name => name !== 'default').sort(), [...names].sort(), `${file}: named states changed`);
  for (const name of names) {
    const scene = sequencing.find(entry => entry.exportName === name);
    assert.equal(`${slug(title)}--${slug(name)}`, scene.id, `${file}/${name}: Storybook ID changed`);
    assert.deepEqual(stories[name].parameters.nativeCapture, { id: scene.id, kind: 'mock', columns: [187, 80] });
    assert.deepEqual(stories[name].parameters.prd, scene.parameters.prd, `${file}/${name}: PRD metadata changed`);
    assert.match(stories[name].parameters.docs.description.story, /Behavioral contract \(synthetic projection\).*Static capture/);
    for (const id of scene.parameters.prd.requirements) sequencingRequirements.add(id);
  }
}
// Visual requirements only: FR-SEQ-9/11/22 (completion rules, reply-Draft storage, agent CLI) have no screen of their own.
for (const n of [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 23, 24]) assert(sequencingRequirements.has(`FR-SEQ-${n}`), `FR-SEQ-${n}: no Sequencing mock`);
for (let n = 21; n <= 32; n++) assert(scenarios.has(`AS-${n}`), `AS-${n}: not covered`);
for (const [file, title] of Object.entries(todayFiles)) {
  const stories = await import(`./src/${file}.stories.js`);
  const names = today.filter(scene => scene.file === `${file}.stories.js`).map(scene => scene.exportName);
  assert.equal(stories.default.title, title, `${file}: group title changed`);
  assert.deepEqual(Object.keys(stories).filter(name => name !== 'default').sort(), [...names].sort(), `${file}: named states changed`);
  for (const name of names) {
    const scene = today.find(entry => entry.exportName === name);
    assert.equal(`${slug(title)}--${slug(name)}`, scene.id, `${file}/${name}: Storybook ID changed`);
    assert.deepEqual(stories[name].parameters.nativeCapture, { id: scene.id, kind: 'mock', columns: [187, 80] });
    assert.deepEqual(stories[name].parameters.prd, scene.parameters.prd, `${file}/${name}: PRD metadata changed`);
  }
}
const referenceStories = await import('./src/reference.stories.js');
assert.equal(referenceStories.default.title, 'Reference current app');
assert.deepEqual(Object.keys(referenceStories).filter(name => name !== 'default').sort(), ['EmailReply', 'People', 'Queue']);
assert.deepEqual(references, [
  { id: 'reference-current-app--queue', state: 'Queue' },
  { id: 'reference-current-app--email-reply', state: 'EmailReply' },
  { id: 'reference-current-app--people', state: 'People' },
]);
for (const scene of references) {
  assert.equal(`${slug(referenceStories.default.title)}--${slug(scene.state)}`, scene.id);
  assert.deepEqual(referenceStories[scene.state].parameters.nativeCapture, { id: scene.id, kind: 'reference', columns: [187, 80] });
  assert.match(referenceStories[scene.state].parameters.docs.description.story, /synthetic data.*Static capture/);
}
const expectedFiles = [...Object.keys(required).map(file => `${file}.stories.js`), ...Object.keys(sequencingFiles).map(file => `${file}.stories.js`), ...Object.keys(todayFiles).map(file => `${file}.stories.js`), 'reference.stories.js', 'snapshot.js', 'styles.css'].sort();
assert.deepEqual((await readdir(local('./src/'))).sort(), expectedFiles, 'Retired HTML renderers or unexpected UI implementation remain');
const snapshotSource = await text('./src/snapshot.js');
assert(!/\b(?:XMLHttpRequest|WebSocket|localStorage|sessionStorage|indexedDB)\b|innerHTML|<(?:form|input|textarea|table|main|nav)\b/.test(snapshotSource), 'Viewer must not recreate app UI or touch providers/storage');
assert.equal((snapshotSource.match(/\bfetch\s*\(/g) || []).length, 1, 'Viewer must only fetch generated assets');
assert.match(snapshotSource, /asset\('\/native\/manifest\.json'\)/);
assert.match(snapshotSource, /credentials: 'omit', redirect: 'error'/);
const styles = await text('./src/styles.css');
assert(!/\.(?:mock-frame|terminal-layout|rail|main-pane|context-pane|form-field|thread-row|message)\b/.test(styles), 'Old app CSS remains');
assert(!/\.native-svg[^{}]*\{[^}]*\bfont-size\s*:/s.test(styles), 'Viewer must not change native glyph geometry');
for (const variant of ['Regular', 'Bold', 'Italic']) {
  assert(styles.includes(`/fonts/VictorMonoNerdFont-${variant}.ttf`), `${variant}: missing local font face`);
  assert((await readFile(local(`./public/fonts/VictorMonoNerdFont-${variant}.ttf`))).length > 0, `${variant}: missing font asset`);
}
assert.match(await text('./.storybook/preview.js'), /order:\s*\[\s*'Reference current app'/);
const packageJson = JSON.parse(await text('./package.json'));
assert.equal(packageJson.scripts['native-captures'], '../.venv/bin/python -B native/render.py');
assert.equal(packageJson.scripts['build-storybook'], 'npm run native-captures && storybook build');
assert((await text('../.gitignore')).split('\n').includes('storybook/public/native/'));

let manifest;
try {
  manifest = JSON.parse(await text('./public/native/manifest.json'));
} catch (error) {
  throw new Error('Native manifest missing or invalid. Run npm run native-captures before npm run check.', { cause: error });
}
assert.equal(manifest.renderer, 'Textual');
assert.deepEqual(manifest.columns, [187, 80]);
assert.equal(manifest.font.family, 'VictorMono Nerd Font');
assert.equal(manifest.font.path, '/fonts/VictorMonoNerdFont-Regular.ttf');
assert.equal(manifest.font.sha256, hash(await readFile(local(`./public${manifest.font.path}`))), 'Local font hash mismatch');
assert.equal(manifest.source.tui_sha256, hash(await readFile(local('../src/comms_terminal/tui.py'))), 'Captures do not match current Textual source');
const expectedScenes = [
  ...catalog.map(scene => ({ id: scene.id, state: scene.exportName, kind: 'mock' })),
  ...references.map(scene => ({ ...scene, kind: 'reference' })),
];
assert.equal(manifest.scenes.length, 2 * expectedScenes.length, 'Expected every scene at both column counts');
const captures = new Map();
const svgPaths = [];
for (const capture of manifest.scenes) {
  const scene = expectedScenes.find(entry => entry.id === capture.id);
  assert(scene && [187, 80].includes(capture.columns), 'Unrecognized native scene or columns');
  assert.equal(capture.state, scene.state);
  assert.equal(capture.kind, scene.kind);
  assert.equal(capture.path, `/native/${scene.id}--${capture.columns}.svg`);
  const key = `${capture.id}--${capture.columns}`;
  assert(!captures.has(key), `${key}: duplicate native capture`);
  const path = local(`./public${capture.path}`);
  const bytes = await readFile(path);
  assert.equal(capture.sha256, hash(bytes), `${key}: SVG hash mismatch`);
  captures.set(key, capture);
  svgPaths.push(fileURLToPath(path));
}
for (const scene of expectedScenes) for (const columns of [187, 80]) assert(captures.has(`${scene.id}--${columns}`), `${scene.id}/${columns}: missing native capture`);

// Python stdlib validates XML; no DOM or new JavaScript parser/test dependency.
const { stdout } = await promisify(execFile)(fileURLToPath(local('../.venv/bin/python')), ['-B', '-c', String.raw`
import sys, re, json, xml.etree.ElementTree as ET
from pathlib import Path
from importlib.metadata import version
storybook = Path(sys.argv[1])
sys.path.insert(0, str(storybook / 'native'))
from scenes import OUTCOMES, scene
accepted_states = {'AcceptedEmail', 'AcceptedSms', 'DuplicateAcceptedReceipt', 'DeliveryFailedReceipt', 'DeliveryCoverageUnavailable', 'RecoveredAccepted'}
assert {name for name, spec in OUTCOMES.items() if spec.get('accepted')} == accepted_states, 'Accepted-complete scene contract changed'
metadata = {Path(capture['path']).name: capture for capture in json.loads((storybook / 'public/native/manifest.json').read_text())['scenes']}
edited = '\U000f0cb6'  # Existing ICON['edited'] / md-pencil_outline.

def plain(lines):
    return re.sub(r'\s+', ' ', re.sub(r'[│╭╮╰╯─]', ' ', '\n'.join(lines))).strip()

namespace = 'http://www.w3.org/2000/svg'
tags = {'svg', 'style', 'defs', 'clipPath', 'rect', 'text', 'tspan', 'g', 'path', 'title', 'desc'}
attributes = {'class', 'id', 'viewBox', 'width', 'height', 'x', 'y', 'rx', 'ry', 'd', 'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-opacity', 'opacity', 'clip-path', 'clipPathUnits', 'shape-rendering', 'text-anchor', 'textLength', 'lengthAdjust', 'transform', 'dx', 'dy', 'font-family', 'font-size', 'font-weight', 'font-style', 'text-decoration', 'dominant-baseline', '{http://www.w3.org/XML/1998/namespace}space'}
for filename in sys.argv[2:]:
    source = Path(filename).read_text()
    assert not re.search(r'<!DOCTYPE|<!ENTITY|<\?', source, re.I), f'{filename}: unsupported XML declaration'
    root = ET.fromstring(source)
    assert root.tag == '{'+namespace+'}svg', f'{filename}: invalid SVG root'
    assert 'native-textual-capture' in root.get('class', '').split(), f'{filename}: missing native canvas marker'
    viewbox = [float(value) for value in root.get('viewBox', '').split()]
    assert len(viewbox) == 4 and viewbox[2] > 0 and viewbox[3] > 0, f'{filename}: invalid native size'
    text_count = 0
    native_styles = {}
    for element in root.iter():
        tag = element.tag.removeprefix('{'+namespace+'}')
        assert element.tag.startswith('{'+namespace+'}') and tag in tags, f'{filename}: unsupported SVG element {tag}'
        assert set(element.attrib) <= attributes, f'{filename}: unsupported SVG attributes'
        values = list(element.attrib.values()) + ([element.text or ''] if tag == 'style' else [])
        for value in values:
            assert not re.search(r'\\|/\*|\*/|@|https?:|data:|javascript:|//|expression\s*\(|var\s*\(', value, re.I), f'{filename}: external resource or unsafe style'
            for url in re.findall(r'url\s*\(([^)]*)\)', value, re.I):
                assert re.fullmatch(r'\s*[\"\']?#[A-Za-z0-9_-]+[\"\']?\s*', url), f'{filename}: external SVG resource'
        if tag == 'text': text_count += 1
        if tag == 'style':
            css = element.text or ''
            assert 'VictorMono Nerd Font' in css, f'{filename}: wrong canvas font'
            assert not re.sub(r'([^{}]+)\{([^{}]*)\}', '', css).strip(), f'{filename}: invalid styles'
            for selector, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
                assert re.fullmatch(r'\s*\.[A-Za-z0-9_-]+\s*', selector), f'{filename}: non-native selector'
                for declaration in filter(str.strip, declarations.split(';')):
                    assert re.match(r'\s*(fill|font-family|font-size|font-weight|font-style|line-height|font-variant-east-asian|text-decoration)\s*:', declaration, re.I), f'{filename}: unsupported SVG style'
                native_styles[selector.strip()[1:]] = {key.strip().lower(): value.strip().lower() for key, value in (declaration.split(':', 1) for declaration in filter(str.strip, declarations.split(';')))}
    assert text_count, f'{filename}: empty native text canvas'
    capture = metadata[Path(filename).name]
    columns = capture['columns']
    cell_width = viewbox[2] / columns
    grid = {}
    cell_styles = {}
    # Rich splits styled labels across text nodes and uses NBSP for terminal spaces.
    for element in root.iter('{'+namespace+'}text'):
        y = round(float(element.get('y', '0')), 3)
        row = grid.setdefault(y, [' '] * columns)
        x = round((float(element.get('x', '0')) - viewbox[0]) / cell_width)
        for offset, character in enumerate(''.join(element.itertext()).replace('\xa0', ' ')):
            if 0 <= x + offset < columns:
                row[x + offset] = character
                cell_styles[y, x + offset] = native_styles.get(element.get('class', ''), {})
    # Native MainScreen: 30-cell rail, 2fr/3fr list/detail, one-row footer.
    rows = [grid[y] for y in sorted(grid)][:-1]
    split = 30 + int((columns - 30) * 2 / 5)
    rail_rows = [''.join(row[:30]) for row in rows]
    main_rows = [''.join(row[30:split]) for row in rows]
    detail_rows = [''.join(row[split:]) for row in rows]
    # Exclude the native properties column before joining wrapped conversation text.
    conversation_rows = [row[:-36] for row in detail_rows] if columns == 187 else detail_rows
    rail, main, detail = map(plain, (rail_rows, main_rows, conversation_rows))
    if capture['id'].startswith('today--'):
        assert re.search(r'\bToday\b', ''.join(rail_rows[:3])), f'{filename}: Today must be the first rail line'
        everything = plain([''.join(row) for row in rows])
        marker = {'TodayLists': 'overdue', 'TodayTasks': 'Call twice', 'TodayAllClear': 'All clear'}[capture['state']]
        assert columns == 80 or marker in everything, f'{filename}: {capture["state"]} lost its marker {marker!r}'  # 80 truncates
        continue
    if capture['id'].startswith('sequencing-'):
        modal = capture['state'] in {'SequenceArchive', 'EnrollFromPeople', 'TasksFilter'} and columns == 80  # a centred menu covers the rail
        if not modal:
            assert rail_rows[0][0] == '╭' and rail_rows[-1] == '╰' + '─' * 28 + '╯', f'{filename}: missing native outer rail frame'
            assert all(row[0] == row[-1] == '│' for row in rail_rows[1:-1]), f'{filename}: native outer rail side borders changed'
        drafts = [(i, m.start()) for i, row in enumerate(rail_rows) for m in re.finditer(r'\bdrafts\b', row, re.I)]
        cases = [(i, m.start()) for i, row in enumerate(rail_rows) for m in re.finditer(r'\bcases\b', row, re.I)]
        playbook = [i for i, row in enumerate(rail_rows) if re.search(r'\bplaybook\b', row)]
        assert len(playbook) == 1 and drafts and drafts[0][0] < playbook[0] < cases[0][0], f'{filename}: playbook section must sit after Drafts, before Cases'
        assert re.search(r'\bTasks\b', rail_rows[playbook[0] + 1]) and re.search(r'\bSequences\b', rail_rows[playbook[0] + 2]), f'{filename}: Tasks/Sequences rail rows missing'
        page = capture['id'].split('--')[0].removeprefix('sequencing-')
        title = {'tasks': r'\btasks\b', 'sequences': r'\bsequences?\b', 'people': r'\bpe(?:ople|rson)\b'}[page]
        assert re.search(title, ''.join(rows[0]), re.I), f'{filename}: page title missing'
        everything = plain([''.join(row) for row in rows])
        for state, marker in {'TasksPausedToast': 'Enrollment paused', 'SequenceStepDeleted': 'moved to the next', 'EnrollRefusedUnsubscribed': 'unsubscribed',
                              'SequenceArchive': 'Archive Secret Sequence', 'EnrollFromPeople': 'Enroll Dana Roy', 'TasksEmpty': 'All caught up',
                              'TasksMissingFillIn': 'Sending is refused', 'TasksReplyTargetSkipped': 'Reply target skipped',
                              'SequencesList': 'Event follow-up', 'SequenceNew': 'No Steps yet', 'TasksFilter': 'Filter Tasks',
                              'PersonSequencePaused': 'Event follow-up'}.items():
            if capture['state'] == state and columns == 187:  # at 80 the markers wrap across interleaved panes
                assert marker in everything, f'{filename}: {state} lost its marker {marker!r}'
        continue
    drafts = [(i, match.start()) for i, row in enumerate(rail_rows) for match in re.finditer(r'\bdrafts\b', row, re.I)]
    assert not re.search(r'\bNew\s+Thread\b', rail, re.I), f'{filename}: top rail must navigate to Drafts, not create New Thread'
    assert rail_rows[0][0] == '╭' and rail_rows[0][-1] == '╮' and rail_rows[-1] == '╰' + '─' * 28 + '╯', f'{filename}: missing native outer rail frame'
    assert all(row[0] == row[-1] == '│' for row in rail_rows[1:-1]), f'{filename}: native outer rail side borders changed'
    threads = [(i, match.start()) for i, row in enumerate(rail_rows) for match in re.finditer(r'\bthreads\b', row, re.I)]
    cases = [(i, match.start()) for i, row in enumerate(rail_rows) for match in re.finditer(r'\bcases\b', row, re.I)]
    browse = [(i, match.start()) for i, row in enumerate(rail_rows) for match in re.finditer(r'\bbrowse\b', row, re.I)]
    assert len(threads) == len(cases) == len(browse) == 1 and threads[0][0] < cases[0][0] < browse[0][0], f'{filename}: native Threads/Cases/Browse categories changed'
    ignored = [i for i, row in enumerate(rail_rows) if threads[0][0] < i < cases[0][0] and re.search(r'\bIgnored\b', row)]
    assert len(ignored) == 1, f'{filename}: missing native Threads Ignored row'
    if capture['kind'] == 'reference':
        # Shipped console (composition released): Drafts, with its count, took the All row under Threads.
        assert len(drafts) == 1 and drafts[0] == (ignored[0] + 1, 5), f'{filename}: current-app reference lost the shipped Drafts row'
        assert not re.search(r'\bAll\b', rail), f'{filename}: current-app reference regained the retired All row'
        assert not re.search(r'\bdrafts\b', main_rows[0], re.I) and not re.search(r'\bdraft\b', detail_rows[0], re.I), f'{filename}: current-app reference gained draft workspace'
        continue
    assert len(drafts) == 1, f'{filename}: expected one Drafts entry, no duplicate Browse/OptionList entry'
    draft_row, label_column = drafts[0]
    assert draft_row == ignored[0] + 1 and threads[0][0] < draft_row < cases[0][0] < browse[0][0], f'{filename}: Drafts must occupy old All row directly after Ignored under Threads, before Cases/Browse'
    assert not re.search(r'\bAll\b', rail), f'{filename}: mock rail must replace All with Drafts'
    glyph_column = label_column - 2
    assert (glyph_column, label_column) == (3, 5), f'{filename}: Drafts glyph/text must align with native page rows, not category headers'
    page_rows = [(i, match.start()) for i, row in enumerate(rail_rows) for match in re.finditer(r'\b(?:Todo|People)\b', row)]
    assert all(column == label_column and rail_rows[i][glyph_column] != ' ' for i, column in page_rows), f'{filename}: Drafts must match Todo/People page-row alignment where available'
    draft_label = f'{edited} Drafts'
    assert rail_rows[draft_row][glyph_column:label_column + len('Drafts')] == draft_label, f'{filename}: missing left-aligned existing pencil glyph before Drafts'
    assert not any(re.search(r'[│╭╮╰╯─]', row[1:-1]) for row in rail_rows[1:-1]), f'{filename}: Drafts must be an ordinary native rail row, not a little boxed button'
    spec = scene(capture['state'])
    drafts_active = spec['group'] in {'compose', 'drafts', 'recipient', 'admission', 'outcome'} and capture['state'] not in accepted_states
    active_rows = [i for i, row in enumerate(rail_rows) if row[glyph_column - 1] == '▌']
    assert len(active_rows) == 1 and (draft_row in active_rows) == drafts_active, f'{filename}: Drafts native active marker does not match current workspace'
    active_y = sorted(grid)[active_rows[0]]
    assert cell_styles[active_y, glyph_column - 1].get('fill') == '#45707a', f'{filename}: selected rail row lost native blue marker'
    assert cell_styles[active_y, label_column].get('fill') == '#4f3829' and cell_styles[active_y, label_column].get('font-weight') in {'bold', '700'}, f'{filename}: selected rail row lost native dark bold label treatment'
    draft_y = sorted(grid)[draft_row]
    assert rail_rows[draft_row][glyph_column - 1] == ('▌' if drafts_active else ' '), f'{filename}: non-Drafts view left Drafts selected'
    assert cell_styles[draft_y, glyph_column].get('fill') == '#654735' and cell_styles[draft_y, glyph_column].get('font-weight', 'normal') in {'normal', '400'}, f'{filename}: Drafts pencil must retain native page glyph styling'
    for column in range(label_column, label_column + len('Drafts')):
        style = cell_styles[draft_y, column]
        assert style.get('fill') == ('#4f3829' if drafts_active else '#654735') and style.get('font-weight', 'normal') in ({'bold', '700'} if drafts_active else {'normal', '400'}), f'{filename}: Drafts label must use native selected/unselected page treatment'
    if capture['state'] in accepted_states:
        assert re.search(r'\bthreads\b', main_rows[0], re.I) and re.search(r'\bthread\b', detail_rows[0], re.I), f'{filename}: accepted draft did not convert to native Threads/Thread'
        assert not re.search(r'\bD-07\b', main) and not re.search(r'\b(?:Plain text|Frozen)\b|\bnew (?:email|SMS)\b', detail, re.I), f'{filename}: accepted draft still listed or editable'
        tid = 'T-105' if spec.get('sms') else 'T-104'
        assert tid in detail and 'D-07 converted' in detail and 'A-04' in detail, f'{filename}: converted Thread lost original attempt/receipt evidence'
    elif spec['group'] in {'compose', 'drafts', 'recipient', 'admission', 'outcome'}:
        assert re.search(r'\bdrafts\b', main_rows[0], re.I) and re.search(r'\bdraft\b', detail_rows[0], re.I), f'{filename}: missing native draft list/detail split'
        assert re.search(r'\bdrafts\b', main, re.I), f'{filename}: draft master table missing'
        if capture['state'] == 'EmptyDrafts':
            assert 'No drafts' in main and 'No draft selected' in detail, f'{filename}: missing empty draft list/detail markers'
            assert not re.search(r'\bD-\d+\b', main + detail) and not re.search(r'\b(?:Plain text|Frozen)\b', detail), f'{filename}: empty workspace selected a draft/editor'
        else:
            assert re.search(r'\b(?:saved|unsaved|sending|unknown|pending|locked)\b', main, re.I), f'{filename}: draft state missing from master list'
            assert re.search(r'\bFrozen\b' if spec.get('frozen') else r'\bPlain text\b', detail), f'{filename}: selected draft editor/frozen marker missing'
            assert not re.search(r'\bD-\d+\b|\brevision\b|\bComposition\b', main), f'{filename}: draft list regressed to diagnostic labels'
            if capture['state'] == 'SavedAndUnresolvedDrafts':
                draft_rows = [i for i, row in enumerate(main_rows) if re.search(r'\b(?:saved|unknown|pending)\b', row)]
                assert len(draft_rows) == 4 and draft_rows == list(range(draft_rows[0], draft_rows[0] + 4)), f'{filename}: draft rows lost one-line Threads density'
                assert all(status in main_rows[index] for index, status in zip(draft_rows, ('saved', 'saved', 'unknown', 'pending'))), f'{filename}: draft status clipped or inventory changed'
            if spec['group'] == 'outcome':
                assert 'A-04' in detail and 'revision 4' in detail, f'{filename}: original attempt/revision missing'
                assert not re.search(r'\bT-10[45]\b|\bconverted\b', detail), f'{filename}: pending/rejected/unknown draft claims completed new Thread'
                assert '0 recorded' in detail, f'{filename}: incomplete outcome lost zero-recorded marker'
                if spec.get('frozen'):
                    assert 'Plain text' not in detail, f'{filename}: unresolved draft is editable'
                if spec.get('pending'):
                    assert 'recording pending' in detail.lower(), f'{filename}: accepted-recording-pending lost pending marker'
print(json.dumps({'textual_version': version('textual')}))
`, fileURLToPath(local('./')), ...svgPaths], { maxBuffer: 1024 * 1024 });
assert.equal(manifest.source.textual_version, JSON.parse(stdout).textual_version, 'Capture Textual runtime version mismatch');
console.log('56 preserved mock states + 17 Sequencing mock states + 3 current-app references; FR-17–29 / AS-21–32 covered; 152 safe native SVGs; single pencil Drafts row replaces All under Threads at page-row cells 3/5 with native active styling and outer rail frame, no special button; draft list/detail and accepted conversion checked; current source, runtime and local font hashes verified.');

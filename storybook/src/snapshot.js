import catalog from '../scene-catalog.json' with { type: 'json' };

export const references = [
  { id: 'reference-current-app--queue', state: 'Queue' },
  { id: 'reference-current-app--email-reply', state: 'EmailReply' },
  { id: 'reference-current-app--people', state: 'People' },
];
const scenes = new Map([
  ...catalog.map(scene => [scene.id, { ...scene, state: scene.exportName, kind: 'mock' }]),
  ...references.map(scene => [scene.id, { ...scene, kind: 'reference' }]),
]);
const columnsAvailable = [187, 80];
const fontFamily = 'VictorMono Nerd Font';
const fontPath = '/fonts/VictorMonoNerdFont-Regular.ttf';
const svgNamespace = 'http://www.w3.org/2000/svg';
const svgTags = new Set(['svg', 'style', 'defs', 'clipPath', 'rect', 'text', 'tspan', 'g', 'path', 'title', 'desc']);
const svgAttributes = new Set([
  'xmlns', 'class', 'id', 'viewBox', 'width', 'height', 'x', 'y', 'rx', 'ry', 'd',
  'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-opacity', 'opacity',
  'clip-path', 'clipPathUnits', 'shape-rendering', 'text-anchor', 'textLength',
  'lengthAdjust', 'transform', 'dx', 'dy', 'font-family', 'font-size', 'font-weight',
  'font-style', 'text-decoration', 'dominant-baseline', 'xml:space',
]);

function validateResources(value) {
  if (/\\|\/\*|\*\/|@|(?:https?:|data:|javascript:|\/\/)|expression\s*\(|var\s*\(/i.test(value)) {
    throw new Error('Capture contains an unsupported resource or style.');
  }
  const urls = [...value.matchAll(/url\s*\(([^)]*)\)/gi)];
  if (/url\s*\(/i.test(value) && !urls.length) throw new Error('Capture contains an invalid SVG resource.');
  for (const [, target] of urls) {
    if (!/^\s*["']?#[A-Za-z0-9_-]+["']?\s*$/.test(target)) throw new Error('Capture contains an external SVG resource.');
  }
}

function parseCapture(source) {
  if (/<!DOCTYPE|<!ENTITY|<\?/i.test(source)) throw new Error('Capture contains an unsupported XML declaration.');
  const parsed = new DOMParser().parseFromString(source, 'image/svg+xml');
  const svg = parsed.documentElement;
  if (parsed.querySelector('parsererror') || svg.localName !== 'svg' || svg.namespaceURI !== svgNamespace) {
    throw new Error('Capture is not a valid SVG.');
  }
  for (const element of [svg, ...svg.querySelectorAll('*')]) {
    if (element.namespaceURI !== svgNamespace || !svgTags.has(element.localName)) throw new Error('Capture contains an unsupported SVG element.');
    for (const attribute of element.attributes) {
      if (!svgAttributes.has(attribute.name)) throw new Error('Capture contains an unsupported SVG attribute.');
      if (attribute.name !== 'xmlns') validateResources(attribute.value);
    }
    if (element.localName === 'style') {
      const css = element.textContent;
      validateResources(css);
      const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
      if (css.replace(/([^{}]+)\{([^{}]*)\}/g, '').trim()) throw new Error('Capture contains invalid SVG styles.');
      for (const [, selector, declarations] of rules) {
        if (!/^\s*\.[A-Za-z0-9_-]+\s*$/.test(selector)) throw new Error('Capture styles must target native SVG classes.');
        for (const declaration of declarations.split(';').filter(part => part.trim())) {
          if (!/^\s*(?:fill|font-family|font-size|font-weight|font-style|line-height|font-variant-east-asian|text-decoration)\s*:/i.test(declaration)) {
            throw new Error('Capture contains an unsupported SVG style.');
          }
        }
      }
    }
  }
  const viewBox = svg.getAttribute('viewBox')?.trim().split(/\s+/).map(Number);
  if (viewBox?.length !== 4 || !viewBox.every(Number.isFinite) || viewBox[2] <= 0 || viewBox[3] <= 0) throw new Error('Capture has no valid native canvas size.');
  svg.setAttribute('width', String(viewBox[2]));
  svg.setAttribute('height', String(viewBox[3]));
  svg.classList.add('native-svg');
  return document.importNode(svg, true);
}

async function asset(path) {
  const response = await fetch(path, { credentials: 'omit', redirect: 'error' });
  if (!response.ok) throw new Error(`Capture asset unavailable: ${path} (${response.status}).`);
  if (new URL(response.url).origin !== window.location.origin) throw new Error('Capture asset must be same-origin.');
  return response;
}

export function snapshot(id, { columns = 187 } = {}) {
  const scene = scenes.get(id);
  if (!scene || !columnsAvailable.includes(columns)) throw new TypeError('Unknown native capture ID or column count.');
  const root = document.createElement('figure');
  root.className = 'native-viewer';
  root.dataset.captureId = id;
  const toolbar = document.createElement('div');
  toolbar.className = 'native-toolbar';
  toolbar.setAttribute('aria-label', 'Capture viewing controls');
  const select = (label, choices, value) => {
    const field = document.createElement('label');
    field.append(`${label} `);
    const control = document.createElement('select');
    for (const [choice, text] of choices) control.add(new Option(text, choice));
    control.value = String(value);
    field.append(control);
    toolbar.append(field);
    return control;
  };
  const columnControl = select('Columns', [[187, '187 · current terminal'], [80, '80 · narrow']], columns);
  const sizeControl = select('View', [['fit', 'Fit width'], ['actual', 'Actual size']], 'fit');
  const status = document.createElement('p');
  status.className = 'native-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const viewport = document.createElement('div');
  viewport.className = 'native-viewport';
  viewport.dataset.size = sizeControl.value;
  viewport.tabIndex = 0;
  viewport.setAttribute('aria-label', 'Static Textual capture; scroll in actual-size view');
  const caption = document.createElement('figcaption');
  const label = scene.kind === 'reference' ? 'Current app reference' : 'Proposed state';
  caption.textContent = `${label} · ${scene.state} · actual Textual widget SVG · synthetic data, not a Ghostty raster screenshot. Static capture: canvas controls do not work; no messages, providers or storage.`;
  root.append(toolbar, status, viewport, caption);
  let request = 0;
  async function load() {
    const current = ++request;
    const selectedColumns = Number(columnControl.value);
    root.dataset.state = 'loading';
    root.dataset.columns = String(selectedColumns);
    root.setAttribute('aria-busy', 'true');
    status.textContent = `Loading ${selectedColumns}-column Textual capture and local font…`;
    viewport.replaceChildren();
    try {
      const manifest = await (await asset('/native/manifest.json')).json();
      const isHash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
      if (!columnsAvailable.includes(selectedColumns) || JSON.stringify(manifest.columns) !== JSON.stringify(columnsAvailable) || manifest.renderer !== 'Textual' || manifest.font?.family !== fontFamily || manifest.font?.path !== fontPath || !isHash(manifest.font?.sha256) || !isHash(manifest.source?.tui_sha256) || !manifest.source?.textual_version || !Array.isArray(manifest.scenes)) throw new Error('Native capture manifest does not match this viewer.');
      const matches = manifest.scenes.filter(entry => entry.id === id && entry.columns === selectedColumns);
      const capture = matches[0];
      const path = `/native/${id}--${selectedColumns}.svg`;
      if (matches.length !== 1 || capture.path !== path || capture.kind !== scene.kind || capture.state !== scene.state || !isHash(capture.sha256)) throw new Error('Selected native capture is missing or invalid in manifest.');
      const fontSpecs = [`20px "${fontFamily}"`, `700 20px "${fontFamily}"`, `italic 20px "${fontFamily}"`];
      const [source, fonts] = await Promise.all([
        asset(path).then(response => response.text()),
        Promise.all(fontSpecs.map(spec => document.fonts.load(spec))),
      ]);
      await document.fonts.ready;
      if (fonts.some(loaded => !loaded.length) || fontSpecs.some(spec => !document.fonts.check(spec))) throw new Error('Local VictorMono Nerd Font variants failed to load.');
      const svg = parseCapture(source);
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', `${scene.state}, ${selectedColumns} columns, static synthetic Textual capture`);
      if (current !== request) return false;
      viewport.append(svg);
      root.dataset.state = 'ready';
      root.dataset.renderer = manifest.renderer;
      root.dataset.captureKind = capture.kind;
      root.dataset.capturePath = capture.path;
      root.dataset.captureSha256 = capture.sha256;
      root.dataset.fontSha256 = manifest.font.sha256;
      root.dataset.sourceTuiSha256 = manifest.source?.tui_sha256;
      status.textContent = `${selectedColumns} columns · font and capture ready · static canvas`;
      return true;
    } catch (error) {
      if (current !== request) return false;
      root.dataset.state = 'error';
      status.textContent = `${error.message} Generate assets with npm run native-captures, then reload this story. No HTML app fallback.`;
      return false;
    } finally {
      if (current === request) root.setAttribute('aria-busy', 'false');
    }
  }
  columnControl.addEventListener('change', () => { root.ready = load(); });
  sizeControl.addEventListener('change', () => { viewport.dataset.size = sizeControl.value; });
  root.ready = load();
  return root;
}

export function nativeStory(exportName, contract) {
  const scene = catalog.find(entry => entry.exportName === exportName);
  if (!scene || !contract) throw new TypeError(`Missing native story contract: ${exportName}`);
  return {
    render: () => snapshot(scene.id),
    parameters: {
      ...scene.parameters,
      nativeCapture: { id: scene.id, kind: 'mock', columns: columnsAvailable },
      docs: { description: { story: `Behavioral contract (synthetic projection): ${contract} Static capture rendered by actual Textual widgets/styles; proposed behavior is not implemented product proof. Canvas controls do not work in Storybook. No provider or storage access.` } },
    },
  };
}

export function referenceStory(state) {
  const scene = references.find(entry => entry.state === state);
  if (!scene) throw new TypeError(`Unknown current-app reference: ${state}`);
  return {
    render: () => snapshot(scene.id),
    parameters: {
      nativeCapture: { id: scene.id, kind: 'reference', columns: columnsAvailable },
      docs: { description: { story: `Current ${state} rendered by unmodified Textual product widgets/styles with synthetic data. Static capture: canvas controls do not work in Storybook. Reference demonstrates current rendering, not new composition implementation or real account activity.` } },
    },
  };
}

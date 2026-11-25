// Topic tabs, view controls, structure list, info card, about dialog. DOM only; talks via the store.
import { state, setState, subscribe } from '../state';
import type { Plane, Vec3 } from '../types';
import { type AppData, topicStructures } from '../data';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

export function initSidebar(data: AppData): void {
  initTabs(data);
  initControls(data);
  renderList(data);
  renderInfo(data);
  initAbout(data);

  subscribe((_s, changed) => {
    if (changed.has('topicId') || changed.has('visible')) renderList(data);
    if (changed.has('selectedId') || changed.has('hoverId')) markList();
    if (changed.has('selectedId') || changed.has('topicId')) renderInfo(data);
    if (changed.has('plane')) markPlane();
    if (changed.has('topicId')) markTabs();
  });
}

/** Apply a topic: visible = its structures, jump to its focus. */
export function applyTopic(data: AppData, topicId: string): void {
  const t = data.manifest.topics.find((x) => x.id === topicId);
  if (!t) return;
  const off = new Set(t.defaultHidden ?? []);
  setState({
    topicId,
    visible: new Set(topicStructures(data, topicId).filter((s) => !off.has(s.group)).map((s) => s.id)),
    selectedId: null,
    ...(t.focus ? { crosshair: t.focus } : {}),
    ...(t.plane ? { plane: t.plane } : {}),
  }, 'topic');
}

function initTabs(data: AppData): void {
  const nav = $('topic-tabs');
  const planned = ['Herniation', 'Raised ICP'];
  for (const t of data.manifest.topics) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = t.name;
    b.dataset.topic = t.id;
    b.setAttribute('role', 'tab');
    b.onclick = () => applyTopic(data, t.id);
    nav.append(b);
  }
  for (const name of planned) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = name; b.disabled = true; b.title = 'Coming soon';
    nav.append(b);
  }
  markTabs();
}

function markTabs(): void {
  document.querySelectorAll<HTMLButtonElement>('#topic-tabs button[data-topic]').forEach((b) =>
    b.setAttribute('aria-selected', String(b.dataset.topic === state.topicId)));
}

/** Show/hide the three viewer panels; the grid reflows to the number shown. At least one stays on. */
function initPanelToggles(): void {
  const panels = [
    { id: 'viewer-ct', label: 'CT' },
    { id: 'viewer-stylised', label: 'Stylised' },
    { id: 'viewer-3d', label: '3D' },
  ];
  const KEY = 'panels-hidden';
  let hidden: string[] = [];
  try { hidden = JSON.parse(localStorage.getItem(KEY) ?? '[]'); } catch { /* storage unavailable */ }
  const host = $('panel-buttons');
  const grid = $('viewers');
  const apply = () => {
    for (const p of panels) {
      const on = !hidden.includes(p.id);
      $(p.id).hidden = !on;
      host.querySelector(`[data-panel="${p.id}"]`)?.setAttribute('aria-pressed', String(on));
    }
    grid.dataset.count = String(panels.length - hidden.length);
    try { localStorage.setItem(KEY, JSON.stringify(hidden)); } catch { /* ignore */ }
  };
  for (const p of panels) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = p.label; b.dataset.panel = p.id;
    b.title = `Show / hide the ${p.label} panel`;
    b.onclick = () => {
      if (hidden.includes(p.id)) hidden = hidden.filter((h) => h !== p.id);
      else if (hidden.length < panels.length - 1) hidden = [...hidden, p.id];
      apply();
    };
    host.append(b);
  }
  hidden = hidden.filter((h) => panels.some((p) => p.id === h)).slice(0, panels.length - 1);
  apply();
}

function initControls(data: AppData): void {
  initPanelToggles();
  const planes: Plane[] = ['axial', 'coronal', 'sagittal'];
  const seg = $('plane-buttons');
  for (const p of planes) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = p[0].toUpperCase() + p.slice(1); b.dataset.plane = p;
    b.onclick = () => setState({ plane: p }, 'ui');
    seg.append(b);
  }
  markPlane();

  const layout = $<HTMLSelectElement>('layout-select');
  layout.value = state.layout;
  layout.onchange = () => setState({ layout: layout.value as 'single' | 'multi' }, 'ui');

  const win = $<HTMLSelectElement>('window-select');
  const vol = data.volumes.baseVolumes.find((v) => v.id === state.baseVolumeId)!;
  for (const w of vol.windows) win.append(new Option(w.label, w.id));
  win.value = state.windowId;
  win.onchange = () => setState({ windowId: win.value }, 'ui');

  const op = $<HTMLInputElement>('opacity-range');
  op.value = String(state.overlayOpacity);
  op.oninput = () => setState({ overlayOpacity: Number(op.value) }, 'ui');

  const clip = $<HTMLInputElement>('clip-check');
  clip.checked = state.clip3d;
  clip.onchange = () => setState({ clip3d: clip.checked }, 'ui');

  const ex = $<HTMLInputElement>('explode-range');
  ex.value = String(state.explode);
  ex.oninput = () => setState({ explode: Number(ex.value) }, 'ui');

  const radio = $<HTMLInputElement>('radio-check');
  radio.checked = state.radiological;
  radio.onchange = () => setState({ radiological: radio.checked }, 'ui');

  // Keyboard: arrows / PageUp / PageDown step the slice in the current plane (Shift = 5 mm).
  window.addEventListener('keydown', (e) => {
    if ((e.target as HTMLElement).closest('input, select, textarea')) return;
    const up = e.key === 'ArrowUp' || e.key === 'PageUp';
    const down = e.key === 'ArrowDown' || e.key === 'PageDown';
    if (!up && !down) return;
    e.preventDefault();
    const axis = state.plane === 'axial' ? 2 : state.plane === 'coronal' ? 1 : 0;
    const c = [...state.crosshair] as Vec3;
    c[axis] += (up ? 1 : -1) * (e.shiftKey ? 5 : 1);
    setState({ crosshair: c }, 'keys');
  });
}

function markPlane(): void {
  document.querySelectorAll<HTMLButtonElement>('#plane-buttons button').forEach((b) =>
    b.setAttribute('aria-pressed', String(b.dataset.plane === state.plane)));
}

function renderList(data: AppData): void {
  const host = $('structure-list');
  host.replaceChildren();
  const topic = data.manifest.topics.find((t) => t.id === state.topicId);
  if (!topic) return;
  for (const gid of topic.groups) {
    const g = data.manifest.groups.find((x) => x.id === gid);
    const items = data.manifest.structures.filter((s) => s.group === gid);
    if (!g || !items.length) continue;
    const box = document.createElement('div');
    box.className = 'group';
    const title = document.createElement('div');
    title.className = 'group-title';
    const allOn = items.every((s) => state.visible.has(s.id));
    const tog = document.createElement('button');
    tog.type = 'button'; tog.textContent = allOn ? 'hide' : 'show';
    tog.onclick = () => {
      const v = new Set(state.visible);
      for (const s of items) { if (allOn) v.delete(s.id); else v.add(s.id); }
      setState({ visible: v }, 'ui');
    };
    title.append(g.name, tog);
    box.append(title);
    for (const s of items) {
      const row = document.createElement('div');
      row.className = 'structure-item';
      row.dataset.id = s.id;
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = state.visible.has(s.id);
      cb.title = 'Show / hide';
      cb.onclick = (e) => e.stopPropagation();
      cb.onchange = () => {
        const v = new Set(state.visible);
        if (cb.checked) v.add(s.id); else v.delete(s.id);
        setState({ visible: v }, 'ui');
      };
      const sw = document.createElement('span');
      sw.className = 'swatch'; sw.style.background = s.color;
      const name = document.createElement('span');
      name.textContent = s.name;
      row.append(cb, sw, name);
      row.onclick = () => setState({ selectedId: state.selectedId === s.id ? null : s.id }, 'list');
      row.onmouseenter = () => setState({ hoverId: s.id }, 'list');
      row.onmouseleave = () => setState({ hoverId: null }, 'list');
      box.append(row);
    }
    host.append(box);
  }
  markList();
}

function markList(): void {
  document.querySelectorAll<HTMLElement>('.structure-item').forEach((el) => {
    el.classList.toggle('selected', el.dataset.id === state.selectedId);
    el.classList.toggle('hover', el.dataset.id === state.hoverId);
  });
}

function renderInfo(data: AppData): void {
  const host = $('info-panel');
  host.replaceChildren();
  const s = state.selectedId ? data.byId.get(state.selectedId) : undefined;
  const topic = data.manifest.topics.find((t) => t.id === state.topicId);
  const h = document.createElement('h3');
  const p = document.createElement('p');
  if (s) {
    h.textContent = s.name;
    const meta = document.createElement('div');
    meta.className = 'meta';
    const sw = document.createElement('span');
    sw.className = 'swatch'; sw.style.background = s.color;
    meta.append(sw, data.manifest.groups.find((g) => g.id === s.group)?.name ?? '');
    p.textContent = s.notes;
    host.append(h, meta, p);
    if (s.ctNotes) {
      const tip = document.createElement('p');
      tip.className = 'ct-tip';
      tip.textContent = 'On CT: ' + s.ctNotes;
      host.append(tip);
    }
  } else if (topic) {
    h.textContent = topic.name;
    p.textContent = topic.intro;
    const hint = document.createElement('p');
    hint.className = 'meta';
    hint.textContent = 'Click a structure in any panel or the list to read about it. Scroll or use ↑/↓ to move through slices.';
    host.append(h, p, hint);
  }
  if (!data.manifest.reviewed) {
    const f = document.createElement('div');
    f.className = 'review-flag';
    f.textContent = 'Teaching text is a draft awaiting clinical review.';
    host.append(f);
  }
}

function initAbout(data: AppData): void {
  const dlg = $<HTMLDialogElement>('about-dialog');
  $('about-btn').onclick = () => {
    const c = $('about-content');
    c.replaceChildren();
    const h = document.createElement('h3');
    h.textContent = 'About';
    const p = document.createElement('p');
    p.textContent = 'An educational tool for ICU registrars. The CT is a single anonymised, defaced, normal head CT from a public research dataset, warped into a standard brain space; the colour labels come from published brain atlases fitted to it, with some stylisation. Not for clinical decision-making.';
    const h2 = document.createElement('h3');
    h2.textContent = 'Data sources';
    const ul = document.createElement('ul');
    const items = [
      ...data.volumes.baseVolumes.map((v) => v.attribution),
      'Lobes, insula, deep grey nuclei, lateral ventricles: Harvard–Oxford cortical and subcortical atlases (FMRIB, University of Oxford; FSL licence, non-commercial use)',
      'Cerebellum and brainstem divisions: Talairach Daemon atlas in MNI space (Lancaster et al. 2000), via nilearn',
      'Arterial territories: Liu C-F et al. Digital 3D Brain MRI Arterial Territories Atlas. Sci Data 10, 74 (2023), doi:10.1038/s41597-022-01923-0 — CC BY 4.0 (NITRC “arterialatlas”); regrouped here into five territories',
      'Corpus callosum, internal capsule, third/fourth ventricles, aqueduct, basal cisterns, fissures/sulci, falx and tentorium: derived from the above and the CT in this project’s pipeline (approximate, for teaching)',
      'Rendering: NiiVue (BSD-2), three.js (MIT)',
    ];
    for (const t of items) { const li = document.createElement('li'); li.textContent = t; ul.append(li); }
    c.append(h, p, h2, ul);
    dlg.showModal();
  };
}

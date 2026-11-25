// Presenter-driven tutorials: each step is a saved view of the whole app (crosshair, plane, scan,
// what's coloured, 3D opacities + camera, arrows). The player only writes to the store, so every
// panel follows. Steps are cumulative (see TutorialStep in types.ts) and pre-resolved, so any step
// can be jumped to directly. Keys: → / Space / PageDown next, ← / PageUp previous, Home/End,
// R reveal, N notes, P presenter mode, H hide caption, Esc exit.
import { state, setState, subscribe, currentSource } from '../state';
import type { AppState } from '../state';
import type { Arrow, CameraView, Plane, Tutorial, TutorialQuestion, TutorialStep, Vec3 } from '../types';
import { type AppData, displayName, expandIds } from '../data';

const SOURCE = 'tutorial';
const GLIDE_MS = 700;

interface Resolved {
  step: TutorialStep;
  topic?: string; scan?: string; window?: string; plane?: Plane; layout?: 'single' | 'multi';
  crosshair?: Vec3; show?: string[]; show3d?: Record<string, number>;
  explode?: number; clip3d?: boolean; slicePlane3d?: boolean; overlayOpacity?: number; camera?: CameraView;
}

const CARRY = ['topic', 'scan', 'window', 'plane', 'layout', 'crosshair', 'show', 'show3d', 'explode', 'clip3d', 'slicePlane3d', 'overlayOpacity', 'camera'] as const;

function resolve(t: Tutorial): Resolved[] {
  const out: Resolved[] = [];
  let prev: Omit<Resolved, 'step'> = {};
  for (const step of t.steps) {
    const r: Resolved = { ...prev, step };
    for (const k of CARRY) if (step[k] !== undefined) (r as unknown as Record<string, unknown>)[k] = step[k];
    // a new scan without its own window resets to the scan's default window
    if (step.scan !== undefined && step.window === undefined) r.window = undefined;
    out.push(r);
    const { step: _s, ...rest } = r;
    prev = rest;
  }
  return out;
}

let data: AppData;
let active: { t: Tutorial; steps: Resolved[]; i: number } | null = null;
let revealed = false;
let feedback = '';
let glideRaf = 0;
let notesWin: Window | null = null;

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

export function initTutorials(d: AppData): void {
  data = d;
  document.addEventListener('keydown', onKey, true);
  subscribe((s, changed) => {
    if (!active || !changed.has('selectedId') || currentSource() === SOURCE) return;
    checkAnswer(s);
  });
  $('tut-prev').onclick = () => go(-1);
  $('tut-next').onclick = () => go(1);
  $('tut-reset').onclick = () => show(active?.i ?? 0, false);
  $('tut-reveal').onclick = () => reveal();
  $('tut-notes').onclick = () => toggleNotes();
  $('tut-present').onclick = () => setPresenting(!document.body.classList.contains('presenting'));
  $('tut-exit').onclick = () => stopTutorial();
  $('tut-hide').onclick = () => document.body.classList.toggle('tut-collapsed');
}

/** Deep link: #tutorial=<id>&step=<n> (1-based). Call once the panels and topic are up. */
export function openTutorialFromHash(): void {
  const m = /tutorial=([\w-]+)(?:&step=(\d+))?/.exec(location.hash);
  if (m) startTutorial(m[1], m[2] ? Number(m[2]) - 1 : 0);
}

export function tutorialById(id: string): Tutorial | undefined {
  return data?.tutorials.find((t) => t.id === id);
}

export function startTutorial(id: string, stepIndex = 0): void {
  const t = tutorialById(id);
  if (!t || !t.steps.length) { console.warn('no tutorial', id); return; }
  active = { t, steps: resolve(t), i: 0 };
  document.body.classList.add('tutorial-on');
  document.body.classList.remove('tut-collapsed');
  let presenting = true;
  try { presenting = localStorage.getItem('tut-presenting') !== '0'; } catch { /* storage unavailable */ }
  setPresenting(presenting);
  $('tut-bar').hidden = false;
  $('tut-name').textContent = t.name;
  show(Math.max(0, Math.min(stepIndex, t.steps.length - 1)), false);
}

export function stopTutorial(): void {
  if (!active) return;
  active = null;
  cancelAnimationFrame(glideRaf);
  $('tut-bar').hidden = true;
  document.body.classList.remove('tutorial-on', 'presenting', 'tut-collapsed');
  setState({ arrows: [], slicePlane3d: true, selectedId: null }, SOURCE);
  history.replaceState(null, '', location.pathname + location.search);
  if (notesWin && !notesWin.closed) notesWin.close();
  notesWin = null;
}

function go(delta: number): void {
  if (!active) return;
  // On a question step, "next" first reveals the answer.
  if (delta > 0 && active.steps[active.i].step.question && !revealed) { reveal(); return; }
  const j = active.i + delta;
  if (j < 0 || j >= active.steps.length) return;
  show(j, true);
}

function show(i: number, glide: boolean): void {
  if (!active) return;
  active.i = i;
  revealed = false;
  feedback = '';
  const r = active.steps[i];
  const vols = data.volumes.baseVolumes;
  const scan = vols.find((v) => v.id === r.scan) ?? vols.find((v) => v.id === state.baseVolumeId) ?? vols[0];
  const win = scan.windows.find((w) => w.id === r.window) ?? scan.windows[0];
  const visible = new Set(expandIds(data, r.show ?? []));
  const opacity3d: Record<string, number> = {};
  for (const [k, v] of Object.entries(r.show3d ?? {})) {
    for (const id of expandIds(data, [k])) { if (v > 0) opacity3d[id] = v; else delete opacity3d[id]; }
  }
  const patch: Partial<AppState> = {
    baseVolumeId: scan.id, windowId: win.id, visible, opacity3d,
    selectedId: r.step.select ?? null,
    arrows: r.step.arrows ?? [],
    explode: r.explode ?? 0,
    clip3d: r.clip3d ?? false,
    slicePlane3d: r.slicePlane3d ?? true,
    ...(r.topic ? { topicId: r.topic } : {}),
    ...(r.plane ? { plane: r.plane } : {}),
    ...(r.layout ? { layout: r.layout } : {}),
    ...(r.overlayOpacity !== undefined ? { overlayOpacity: r.overlayOpacity } : {}),
    // a fresh object each time, so the 3D panel re-glides even to the same view after the user orbited
    ...(r.camera ? { camera3d: { ...r.camera } } : {}),
  };
  setState(patch, SOURCE);
  if (r.crosshair) glideTo(r.crosshair, glide);
  syncControls();
  render();
  history.replaceState(null, '', `#tutorial=${active.t.id}&step=${i + 1}`);
}

/** Move the crosshair smoothly so the audience sees the slices travel. */
function glideTo(target: Vec3, glide: boolean): void {
  cancelAnimationFrame(glideRaf);
  const from = [...state.crosshair] as Vec3;
  const dist = Math.hypot(target[0] - from[0], target[1] - from[1], target[2] - from[2]);
  if (!glide || dist < 0.5) { setState({ crosshair: target }, SOURCE); return; }
  const t0 = performance.now();
  const dur = Math.min(1400, GLIDE_MS + dist * 6);
  const tick = (now: number) => {
    const t = Math.min(1, (now - t0) / dur);
    const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    const c = from.map((v, k) => v + (target[k] - v) * e) as Vec3;
    setState({ crosshair: t >= 1 ? target : c }, SOURCE);
    if (t < 1) glideRaf = requestAnimationFrame(tick);
  };
  glideRaf = requestAnimationFrame(tick);
}

function checkAnswer(s: AppState): void {
  const q = active?.steps[active.i].step.question;
  if (!q || revealed || !s.selectedId) return;
  const st = data.byId.get(s.selectedId);
  if (q.answer.includes(s.selectedId)) { reveal(true); return; }
  feedback = st ? `Not quite — that's the ${displayName(st).toLowerCase()}. Try again.` : 'Try again.';
  render();
}

function reveal(correct = false): void {
  const r = active?.steps[active.i];
  const q = r?.step.question;
  if (!r || !q || revealed) return;
  revealed = true;
  feedback = correct ? '✓ Correct.' : '';
  const extra: Arrow[] = q.revealArrows ?? [];
  setState({ selectedId: q.answer[0], arrows: [...(r.step.arrows ?? []), ...extra] }, SOURCE);
  render();
}

function render(): void {
  if (!active) return;
  const { t, i } = active;
  const step = active.steps[i].step;
  const q: TutorialQuestion | undefined = step.question;
  $('tut-count').textContent = `${i + 1} / ${t.steps.length}`;
  $('tut-title').textContent = step.title;
  const cap = $('tut-caption');
  cap.replaceChildren();
  if (q && !revealed) {
    cap.append(textEl('span', 'tut-q', q.prompt));
    if (feedback) cap.append(textEl('span', 'tut-feedback', feedback));
  } else if (q) {
    if (feedback) cap.append(textEl('span', 'tut-feedback ok', feedback + ' '));
    cap.append(textEl('span', '', q.reveal));
  } else {
    cap.textContent = step.caption;
  }
  $<HTMLButtonElement>('tut-prev').disabled = i === 0;
  $<HTMLButtonElement>('tut-next').disabled = i === t.steps.length - 1 && (!q || revealed);
  $('tut-reveal').hidden = !q || revealed;
  const dots = $('tut-dots');
  dots.replaceChildren(...t.steps.map((s, k) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'tut-dot' + (k === i ? ' on' : '') + (s.question ? ' q' : '');
    b.title = `${k + 1}. ${s.title}`;
    b.onclick = () => show(k, true);
    return b;
  }));
  $('tut-notes-text').textContent = step.notes ?? '';
  updateNotesWindow();
}

function textEl(tag: string, cls: string, text: string): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  e.textContent = text;
  return e;
}

/** Keep the sidebar controls that aren't store-driven in step with what the tutorial set. */
function syncControls(): void {
  const set = (id: string, v: string | boolean) => {
    const e = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
    if (!e) return;
    if (typeof v === 'boolean') (e as HTMLInputElement).checked = v; else e.value = v;
  };
  set('layout-select', state.layout);
  set('opacity-range', String(state.overlayOpacity));
  set('clip-check', state.clip3d);
  set('explode-range', String(state.explode));
}

function setPresenting(on: boolean): void {
  document.body.classList.toggle('presenting', on);
  $('tut-present').setAttribute('aria-pressed', String(on));
  try { localStorage.setItem('tut-presenting', on ? '1' : '0'); } catch { /* ignore */ }
}

// ---- speaker notes: a separate window (for a second screen), falling back to an inline drawer
function toggleNotes(): void {
  if (notesWin && !notesWin.closed) { notesWin.close(); notesWin = null; return; }
  notesWin = window.open('', 'neuroanatomy-notes', 'width=520,height=640');
  if (!notesWin) { document.body.classList.toggle('tut-notes-open'); return; }
  const d = notesWin.document;
  d.title = 'Speaker notes';
  d.body.innerHTML = '';
  const style = d.createElement('style');
  style.textContent = `body{font:16px/1.5 system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:18px}
    h1{font-size:14px;color:#9aa4b2;margin:0 0 4px;font-weight:500} h2{font-size:22px;margin:0 0 12px}
    #notes{white-space:pre-wrap;font-size:18px} #next{margin-top:24px;color:#9aa4b2;font-size:14px}
    .k{margin-top:24px;color:#6b7480;font-size:12px}`;
  d.head.append(style);
  d.body.insertAdjacentHTML('beforeend', '<h1 id="count"></h1><h2 id="title"></h2><div id="notes"></div><div id="next"></div><div class="k">Keys in this window: → next · ← back · R reveal</div>');
  notesWin.addEventListener('keydown', onKey, true);
  updateNotesWindow();
}

function updateNotesWindow(): void {
  if (!active || !notesWin || notesWin.closed) return;
  const d = notesWin.document;
  const { t, i } = active;
  const step = t.steps[i];
  const g = (id: string) => d.getElementById(id);
  if (!g('title')) return;
  g('count')!.textContent = `${t.name} — step ${i + 1} of ${t.steps.length}`;
  g('title')!.textContent = step.title;
  g('notes')!.textContent = [step.notes ?? '(no notes)', step.question ? `\nAnswer: ${step.question.reveal}` : ''].join('');
  g('next')!.textContent = i + 1 < t.steps.length ? `Next: ${t.steps[i + 1].title}` : 'Last step';
}

function onKey(e: KeyboardEvent): void {
  if (!active) return;
  if ((e.target as Element | null)?.closest?.('input, select, textarea')) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key;
  let handled = true;
  if (k === 'ArrowRight' || k === 'PageDown' || k === ' ') go(1);
  else if (k === 'ArrowLeft' || k === 'PageUp') go(-1);
  else if (k === 'Home') show(0, true);
  else if (k === 'End') show(active.steps.length - 1, true);
  else if (k === 'r' || k === 'R') reveal();
  else if (k === 'n' || k === 'N') toggleNotes();
  else if (k === 'p' || k === 'P') setPresenting(!document.body.classList.contains('presenting'));
  else if (k === 'h' || k === 'H') document.body.classList.toggle('tut-collapsed');
  else if (k === 'Escape') stopTutorial();
  else handled = false;
  if (handled) { e.preventDefault(); e.stopImmediatePropagation(); }
}

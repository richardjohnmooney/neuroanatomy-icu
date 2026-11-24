// Single app store with tiny pub/sub. All panels read from and write to this; none talk to each other directly.
import type { Plane, Vec3 } from './types';

export interface AppState {
  crosshair: Vec3;              // MNI mm
  plane: Plane;                 // plane used for single-plane layouts + 3D slice plane orientation
  layout: 'single' | 'multi';   // slice panels: one large plane, or axial+coronal+sagittal
  topicId: string;
  baseVolumeId: string;         // which BaseVolume the left panel shows (CT now, MRI later)
  windowId: string;             // WindowPreset.id
  visible: Set<string>;         // structure ids shown in overlays/3D
  selectedId: string | null;    // structure shown in info card / highlighted
  hoverId: string | null;
  overlayOpacity: number;       // 0..1 colour over CT
  clip3d: boolean;              // cut 3D model at the crosshair plane
  explode: number;              // 0..1 explode lobes in 3D
  radiological: boolean;        // true: patient left on screen right (CT convention)
}

type Listener = (s: AppState, changed: Set<keyof AppState>) => void;

const listeners = new Set<Listener>();

export const state: AppState = {
  crosshair: [0, -18, 18],
  plane: 'axial',
  layout: 'single',
  topicId: 'lobes',
  baseVolumeId: 'ct',
  windowId: 'brain',
  visible: new Set(),
  selectedId: null,
  hoverId: null,
  overlayOpacity: 0.45,
  clip3d: true,
  explode: 0,
  radiological: true,
};

/** Merge a patch and notify. Pass `source` to let the originating panel skip its own echo. */
export function setState(patch: Partial<AppState>, source?: string): void {
  const changed = new Set<keyof AppState>();
  for (const k of Object.keys(patch) as (keyof AppState)[]) {
    const v = patch[k];
    if (v === undefined) continue;
    const cur = state[k];
    if (k === 'crosshair') {
      const a = cur as Vec3, b = v as Vec3;
      if (Math.abs(a[0] - b[0]) < 1e-3 && Math.abs(a[1] - b[1]) < 1e-3 && Math.abs(a[2] - b[2]) < 1e-3) continue;
    } else if (cur === v) continue;
    (state as unknown as Record<string, unknown>)[k] = v;
    changed.add(k);
  }
  if (changed.size === 0) return;
  lastSource = source;
  for (const l of listeners) l(state, changed);
}

let lastSource: string | undefined;
/** The `source` passed to the setState call currently being dispatched. */
export function currentSource(): string | undefined { return lastSource; }

export function subscribe(l: Listener): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

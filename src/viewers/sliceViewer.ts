// NiiVue-based 2D slice viewer. Two instances: 'base' (greyscale volume + label overlays) and
// 'stylised' (label layers only, flat colours). Linked to other panels only through the store.
import { Niivue, SLICE_TYPE, SHOW_RENDER, NVImage } from '@niivue/niivue';
import type { AppData } from '../data';
import { displayName } from '../data';
import { state, setState, subscribe, currentSource } from '../state';
import type { AppState } from '../state';
import type { Plane, Structure, Vec3 } from '../types';

export interface SliceViewerOptions {
  canvas: HTMLCanvasElement; mode: 'base' | 'stylised'; data: AppData; readout: HTMLElement; sourceId: string;
}

const CROSSHAIR: number[] = [0.31, 0.7, 1, 1]; // #4fb3ff
const PLANE_TYPE: Record<Plane, SLICE_TYPE> = {
  axial: SLICE_TYPE.AXIAL, coronal: SLICE_TYPE.CORONAL, sagittal: SLICE_TYPE.SAGITTAL,
};

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.replace('#', ''), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export async function createSliceViewer(o: SliceViewerOptions): Promise<{ dispose(): void }> {
  const { canvas, mode, data, readout, sourceId } = o;
  const layers = data.volumes.labelLayers;
  const stylised = mode === 'stylised';

  const nv = new Niivue({
    backColor: [0, 0, 0, 1],
    crosshairColor: CROSSHAIR,
    crosshairWidth: 1,
    isColorbar: false,
    isOrientCube: false,
    isRuler: false,
    show3Dcrosshair: false,
    multiplanarShowRender: SHOW_RENDER.NEVER,
    isResizeCanvas: false,
    isNearestInterpolation: true,
    isOrientationTextVisible: true,
    loadingText: '',
    isRadiologicalConvention: state.radiological,
  } as ConstructorParameters<typeof Niivue>[0]);

  try {
    await nv.attachToCanvas(canvas);
  } catch (e) {
    console.error(e);
    readout.textContent = 'WebGL2 is not available - the slice viewer cannot run.';
    return { dispose() {} };
  }

  // ---- volume / label bookkeeping -------------------------------------------------------
  let labelVols: { layerId: string; vol: NVImage }[] = [];
  let baseVol: NVImage | null = null;
  let loadGen = 0;
  let applying = false; // true while we push store state into NiiVue (suppresses re-emit)

  /** Build a 256-entry RGBA label LUT for a layer reflecting visibility / selection / hover. */
  function buildLut(layerId: string) {
    const structs = data.byLabel.get(layerId);
    const maxVal = Math.max(1, ...[...(structs?.keys() ?? [])]);
    const lut = new Uint8ClampedArray((maxVal + 1) * 4);
    const sel = state.selectedId;
    if (structs) {
      for (const [val, s] of structs) {
        if (val < 1 || !state.visible.has(s.id)) continue;
        let [r, g, b] = hexToRgb(s.color);
        let a = 255;
        const isSel = s.id === sel;
        if (sel && !isSel) {
          if (stylised) { r *= 0.55; g *= 0.55; b *= 0.55; } else a = 150;
        }
        if (s.id === state.hoverId) { // light highlight toward white
          r += (255 - r) * 0.3; g += (255 - g) * 0.3; b += (255 - b) * 0.3;
          a = 255;
        }
        const i = val * 4;
        lut[i] = r; lut[i + 1] = g; lut[i + 2] = b; lut[i + 3] = a;
      }
    }
    return { lut, min: 0, max: maxVal };
  }

  function refreshLuts(): void {
    for (const lv of labelVols) lv.vol.colormapLabel = buildLut(lv.layerId);
    nv.refreshColormaps();
    nv.updateGLVolume();
  }

  function applyWindow(): void {
    if (stylised || !baseVol) return;
    const bv = data.volumes.baseVolumes.find((v) => v.id === state.baseVolumeId);
    const w = bv?.windows.find((x) => x.id === state.windowId) ?? bv?.windows[0];
    if (!w) return;
    baseVol.cal_min = w.level - w.width / 2;
    baseVol.cal_max = w.level + w.width / 2;
    nv.updateGLVolume();
  }

  function applyOpacity(): void {
    if (stylised) return;
    labelVols.forEach((lv) => {
      const idx = nv.volumes.indexOf(lv.vol);
      if (idx >= 0) nv.setOpacity(idx, state.overlayOpacity);
    });
  }

  async function loadVolumes(): Promise<void> {
    const gen = ++loadGen;
    const list: Record<string, unknown>[] = [];
    if (stylised) {
      // Label layers only. First layer doubles as NiiVue's mandatory base volume.
      layers.forEach((l) => list.push({ url: data.url(l.file), name: l.file, colormap: 'gray', opacity: 1, colormapLabel: buildLut(l.id) }));
    } else {
      const bv = data.volumes.baseVolumes.find((v) => v.id === state.baseVolumeId) ?? data.volumes.baseVolumes[0];
      list.push({ url: data.url(bv.file), name: bv.file, colormap: 'gray', opacity: 1 });
      layers.forEach((l) => list.push({ url: data.url(l.file), name: l.file, colormap: 'gray', opacity: state.overlayOpacity, colormapLabel: buildLut(l.id) }));
    }
    // Remove existing volumes (when reloading after a base volume change).
    for (const v of [...nv.volumes]) nv.removeVolume(v);
    await nv.loadVolumes(list as never);
    if (gen !== loadGen) return;
    const vols = nv.volumes;
    labelVols = [];
    if (stylised) {
      layers.forEach((l, i) => vols[i] && labelVols.push({ layerId: l.id, vol: vols[i] }));
      baseVol = null;
    } else {
      baseVol = vols[0] ?? null;
      layers.forEach((l, i) => vols[i + 1] && labelVols.push({ layerId: l.id, vol: vols[i + 1] }));
    }
    nv.refreshColormaps();
    applyWindow();
    applyOpacity();
    nv.updateGLVolume();
    applyView();
    applyCrosshair();
  }

  // ---- state -> NiiVue ------------------------------------------------------------------
  function applyView(): void {
    applying = true;
    try {
      nv.setRadiologicalConvention(state.radiological);
      nv.setSliceType(state.layout === 'multi' ? SLICE_TYPE.MULTIPLANAR : PLANE_TYPE[state.plane]);
    } finally { applying = false; }
  }

  function applyCrosshair(): void {
    if (!nv.volumes.length) return;
    applying = true;
    try {
      const frac = nv.mm2frac(state.crosshair as unknown as [number, number, number]);
      nv.scene.crosshairPos = frac;
      nv.drawScene();
    } finally { applying = false; }
  }

  const unsub = subscribe((s: AppState, changed) => {
    if (!nv.volumes.length) return;
    if (changed.has('baseVolumeId') && !stylised) { void loadVolumes(); return; }
    if (changed.has('radiological') || changed.has('layout') || changed.has('plane')) applyView();
    if (changed.has('windowId')) applyWindow();
    if (changed.has('overlayOpacity')) { applyOpacity(); nv.drawScene(); }
    if (changed.has('visible') || changed.has('selectedId') || changed.has('hoverId')) refreshLuts();
    if (changed.has('crosshair') && currentSource() !== sourceId) applyCrosshair();
    else if (changed.has('layout') || changed.has('plane') || changed.has('radiological')) applyCrosshair();
    void s;
  });

  // ---- NiiVue -> store ------------------------------------------------------------------
  nv.onLocationChange = (loc: unknown) => {
    if (applying) return;
    const mm = (loc as { mm?: ArrayLike<number> }).mm;
    if (!mm) return;
    const c: Vec3 = [mm[0], mm[1], mm[2]];
    if (![...c].every(Number.isFinite)) return;
    setState({ crosshair: c }, sourceId);
  };

  // ---- hover / click sampling -----------------------------------------------------------
  function mmAtEvent(e: MouseEvent): Vec3 | null {
    const rect = canvas.getBoundingClientRect();
    const dpr = canvas.width / Math.max(1, rect.width);
    const x = (e.clientX - rect.left) * dpr;
    const y = (e.clientY - rect.top) * dpr;
    let frac: ArrayLike<number>;
    try { frac = nv.canvasPos2frac([x, y]); } catch { return null; }
    if (!frac || frac[0] < 0 || frac[1] < 0 || frac[2] < 0 || frac[0] > 1 || frac[1] > 1 || frac[2] > 1) return null;
    const mm = nv.frac2mm(frac as never);
    return [mm[0], mm[1], mm[2]];
  }

  function structureAt(mm: Vec3): Structure | null {
    for (const lv of labelVols) {
      const vox = lv.vol.mm2vox(mm) as ArrayLike<number>;
      const dims = lv.vol.hdr?.dims;
      const img = lv.vol.img as unknown as ArrayLike<number> | undefined;
      if (!dims || !img) continue;
      const nx = dims[1], ny = dims[2], nz = dims[3];
      const i = Math.round(vox[0]), j = Math.round(vox[1]), k = Math.round(vox[2]);
      if (i < 0 || j < 0 || k < 0 || i >= nx || j >= ny || k >= nz) continue;
      const v = img[i + j * nx + k * nx * ny];
      if (!v) continue;
      const s = data.byLabel.get(lv.layerId)?.get(v);
      if (s && state.visible.has(s.id)) return s;
    }
    return null;
  }

  const onMove = (e: MouseEvent) => {
    const mm = mmAtEvent(e);
    const s = mm ? structureAt(mm) : null;
    readout.textContent = s && mm ? displayName(s, mm) : '';
    setState({ hoverId: s ? s.id : null }, sourceId);
  };
  const onLeave = () => { readout.textContent = ''; setState({ hoverId: null }, sourceId); };
  const onClick = (e: MouseEvent) => {
    const mm = mmAtEvent(e);
    const s = mm ? structureAt(mm) : null;
    if (s) setState({ selectedId: s.id }, sourceId);
  };
  canvas.addEventListener('mousemove', onMove);
  canvas.addEventListener('mouseleave', onLeave);
  canvas.addEventListener('click', onClick);

  // ---- resize ---------------------------------------------------------------------------
  function resize(): void {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w; canvas.height = h;
    }
    if (nv.volumes.length) nv.drawScene();
  }
  const ro = new ResizeObserver(() => resize());
  ro.observe(canvas);

  await loadVolumes();
  resize();

  return {
    dispose() {
      unsub();
      ro.disconnect();
      canvas.removeEventListener('mousemove', onMove);
      canvas.removeEventListener('mouseleave', onLeave);
      canvas.removeEventListener('click', onClick);
    },
  };
}

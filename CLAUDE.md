# CLAUDE.md — NeuroAnatomy for ICU

Interactive web app teaching basic neuroanatomy to ICU registrars: a real CT template sliced in three planes with colour annotations, a stylised colour slice, and a 3D stylised brain — all **linked** (one crosshair drives everything). Hosted as a static site (GitHub Pages planned). Plan of record: `C:\Users\richa\.claude\plans\i-d-like-a-3d-velvety-parrot-of-64ff5287-49a5-4e49-80ad-f3bc9982f8e5.md`.

**Keep this file updated** at the end of every working session (status, decisions, known issues).

## Run
```powershell
npm run deps       # first time / after changing package.json: installs into C:\node_modules\ct-brain-anatomy
npm run dev        # http://localhost:5173
npm run build      # typecheck + static build to dist/
```
**node_modules lives on C:, not in the project.** npm fails with EBADF writing onto the Google Drive volume, and Drive can't hold junctions. So deps go in `C:\node_modules\ct-brain-anatomy\node_modules` (override with `DEPS_DIR`). `tools/run.mjs` runs vite/tsc from there; `vite.config.mjs` aliases `three` / `@niivue/niivue` to it; `tsconfig.json` `paths` point types there. If a local `node_modules/` exists (CI), everything uses it normally. **Never `npm install` in the project folder.** Adding a dependency: edit package.json, then `npm run deps`, then add an alias in `vite.config.mjs` + a `paths` entry in `tsconfig.json`.
Data pipeline (Python, uv — venv lives on C: per the root CLAUDE.md):
```powershell
$env:UV_PROJECT_ENVIRONMENT = "C:\venvs\ct-brain-anatomy"
cd pipeline; uv sync; uv run python run_all.py
```
Pipeline outputs are committed to `public/data/` so the site never needs the pipeline at runtime.

## Architecture
- **Vite + TypeScript**, no framework. `src/main.ts` boots: load manifests → sidebar → 2 slice viewers + 3D viewer → apply topic.
- **`src/state.ts`** — the single store (`state`, `setState(patch, source)`, `subscribe`). Panels never talk to each other; they read/write the store. `source` lets a panel ignore its own echo.
- **`src/types.ts`** — data contracts for `public/data/volumes.json` and `public/data/structures.json`.
- **`src/data.ts`** — loads manifests, lookup maps, `displayName()` (adds L/R from MNI x).
- **`src/viewers/sliceViewer.ts`** — NiiVue. `mode: 'base'` = CT (or later MRI) + colour label overlay; `mode: 'stylised'` = label map only, flat colours.
- **`src/viewers/model3d.ts`** — three.js, GLB meshes per structure, slice plane + clipping at the crosshair, explode, picking.
- **`src/ui/sidebar.ts`** — topic tabs, controls, structure list, info card, About/sources dialog.
- **`pipeline/`** — fetch sources → build label volumes → marching-cubes meshes.
- Panel toggles (CT / Stylised / 3D) live in `sidebar.ts` `initPanelToggles()`: set `hidden` on the `<figure>`s and `data-count` on `#viewers` (CSS reflows the grid); remembered per browser in localStorage; at least one panel stays on. Viewers re-fit via their ResizeObservers.

### Conventions
- **All coordinates are MNI152 world mm, RAS** (+x patient Right, +y Anterior, +z Superior). Meshes are stored in the same space; three.js rotates the scene so +z is up.
- **One grid**: every volume (CT, future MRI, every label layer) is resampled to the FSL MNI152 1 mm grid (182×218×182).
- **`structures.json` is the single source of truth** for structure id, name, group, layer, labelValue, colour, mesh files and teaching notes. Pipeline reads it; all panels read it. Add a structure there first.
- Label layers are separate uint8 volumes (`labels_<layer>.nii.gz`) so overlapping concepts (anatomy vs arterial territories vs dura) don't fight. labelValue ranges in `anatomy`: 1–9 lobes, 10–19 posterior fossa, 20–29 deep grey, 30–49 CSF spaces (31 3rd ventricle, 32 aqueduct, 33 4th ventricle, 40+ cisterns reserved), 50–59 white matter (50 corpus callosum, 51 internal capsule).
- Base volumes listed in `volumes.json` (`baseVolumes[]`) — **MRI is a later phase**: add T1/T2 templates as extra base volumes; nothing else should be CT-specific. Window presets are per volume.
- CT displayed in **radiological convention** by default (toggle in sidebar).
- Teaching text is drafted by Claude: `reviewed: false` in structures.json until the user (clinician) signs it off.

## Working method (token economy)
Opus (main session) owns architecture, contracts (`types.ts`, `state.ts`, manifests), anatomy content/teaching text, hand-authored coordinates (vessels, dura), cross-panel sync and review. Self-contained, well-specified pieces go to **Sonnet subagents** (`model: "sonnet"`), in parallel when they touch different files: pipeline scripts, individual viewer modules, per-layer generators, per-topic modules, and web lookups. Each brief names files, interface, and a done check; no two agents edit the same file; Opus reviews each result before accepting.

## Data sources & licences
| Layer | Source | Licence |
|---|---|---|
| CT | Rorden Clinical Toolbox `scct.nii` (github.com/neurolabusc/Clinical). 2 mm, skull on, stored in **Cormack units** → pipeline inverts to HU (brain ≈29 HU, ventricles ≈2 HU), upsampled to 1 mm. Bone only ≈400 HU due to 2 mm averaging. | BSD |
| Lobes (48 HO cortical regions → 6 lobes, WM → nearest lobe), insula, deep grey, lateral ventricles | Harvard–Oxford cortical + subcortical (FSL, via nilearn) | FSL licence, non-commercial |
| Cerebellum, midbrain/pons/medulla | Talairach Daemon atlas in MNI space (via nilearn) | free for research/education, attribute |
| Corpus callosum, internal capsule | derived in pipeline | own work |
| Planned: arterial territories | Liu et al. 2023 arterial territory atlas (NITRC) | CC BY 4.0 |
| Planned: vessels, falx/tentorium, fissure landmarks | hand-authored / derived in pipeline | own work |
(Update with exact URLs once the pipeline report is in — see `pipeline/README.md`.)

## Status / roadmap
- [x] **Phase 1** (2026-11-25) — scaffold, pipeline (CT + 16 anatomy labels + 28 meshes), three linked panels (CT+overlay, stylised slice, 3D with slice plane/clip/explode/picking), topic tabs, info cards, About/sources. Typecheck + build pass, no console errors. Not yet committed.
- [x] **Better lobes** (2026-11-25) — lobes rebuilt from 48 Harvard–Oxford cortical regions (`LOBE_MAP` in build_labels.py), white matter → nearest lobe per hemisphere, mode-filtered. Added corpus callosum (50) and internal capsule (51), derived geometrically before the WM fill. Check images: `pipeline/check_labels.py` → `pipeline/cache/check_*.png`.
- [x] **Phase 2** (2026-11-25) — CSF: 3rd/4th ventricles, aqueduct, suprasellar/interpeduncular/ambient/quadrigeminal/prepontine cisterns, cisterna magna (anatomy layer 31–33, 40–45; `add_csf_labels` in build_labels.py, CT HU thresholds inside ROIs + geometric fallbacks). New layers `sulci` (interhemispheric, Sylvian, central, parieto-occipital, calcarine) and `dura` (falx, tentorium with notch) from `build_overlays.py`. Topics: CSF system, Fissures & sulci, Falx & tentorium (topics can set `plane` and `defaultHidden` groups). Check images: `pipeline/cache/check_*.png`, `check_overlay_*.png`.
- [ ] Phase 3 — vessels (Circle of Willis, major arteries, venous sinuses), arterial territories.
- [ ] Phase 4 — deploy to GitHub Pages (needs user OK to create repo/push).
- [ ] Phase 5 — ICU extras: herniation syndromes, raised-ICP CT checklist, "where is the blood?", ASPECTS, EVD/ICP bolt, windowing, quiz.
- [ ] Phase 6 — MRI (MNI152 T1/T2), modality toggle and CT↔MRI compare.

Implementation notes: slice viewer hover tests label layers top-down (last layer in volumes.json first). A missing layer file makes NiiVue fail to load the whole viewer (Vite serves index.html for missing files) — always build all layers listed in volumes.json. NiiVue 0.69 label LUT must use `max` = real max labelValue (not 255) or background takes a label colour; hidden structures = alpha 0 in the LUT. three.js imports use `three/examples/jsm/...` (not `three/addons/...`) so tsconfig `paths` resolve. 3D camera refits to the panel aspect on resize. JS bundle ~1.5 MB (NiiVue) — code-split later if load time matters.

Backlog ideas: CT↔stylised wipe/curtain slider, sharper CT source, outline-only overlay mode, midline-shift measuring tool.

## Known simplifications
- Atlas labels on a template are approximate; lobe boundaries follow Harvard–Oxford maxprob (25%) regions.
- Brainstem divisions come from the Talairach atlas, not true landmark segmentation (pons centroid z≈−27); Talairach now only used for cerebellum/brainstem.
- Lobe volumes: frontal 39%, temporal 19%, occipital 18% (a bit large), parietal 17% (a bit small), cingulate 5%, insula 1% — HO extents. Frontal/temporal white-matter boundary is a flat horizontal edge. L/R split by HO hemisphere labels.
- Corpus callosum/internal capsule are geometric approximations: splenium thick, posterior limb of IC thin. Third ventricle region unlabelled (comes with CSF phase).
- CSF spaces: template CT barely resolves cisterns, so 3rd ventricle, suprasellar and ambient are largely geometric (median HU 15–23); ambient/suprasellar oversized (~3.5 mL each); cisterna magna sits low (z≈−60) linked to the 4th ventricle by a straight 'Magendie' tube; no cerebellar tonsil label.
- Sulci/dura are derived from atlas boundaries, not CT. Central sulcus has a gap at |x|≈18–40 on high axial slices and meets the midline at y≈−20 (real ≈−35). Sylvian fissure has no posterior limb beyond y −30. Tentorium's anterior part is a height field, not a true petrous attachment.
- CT template is a 2 mm population average: soft, bone under-represented. A sharper CT source would help (backlog).

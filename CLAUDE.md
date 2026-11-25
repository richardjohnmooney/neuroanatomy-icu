# CLAUDE.md — NeuroAnatomy for ICU

Interactive web app teaching basic neuroanatomy to ICU registrars: a real CT template sliced in three planes with colour annotations, a stylised colour slice, and a 3D stylised brain — all **linked** (one crosshair drives everything). Live at https://richardjohnmooney.github.io/neuroanatomy-icu/ (GitHub Pages, deployed on every push to `main`). Plan of record: `C:\Users\richa\.claude\plans\i-d-like-a-3d-velvety-parrot-of-64ff5287-49a5-4e49-80ad-f3bc9982f8e5.md`.

**Keep this file updated** at the end of every working session (status, decisions, known issues).

## Location, repo, environment
- **Project lives at `C:\dev\ct-brain-anatomy`** (moved off Google Drive 2026-11-25; the old `G:\My Drive\claude\CT Brain anatomy` copy is obsolete). Cloud copy: public GitHub repo `richardjohnmooney/neuroanatomy-icu`; the app deploys to GitHub Pages from `main` via `.github/workflows/deploy.yml`.
- Commits use the GitHub no-reply email (set in this repo's git config) — keep it that way; the repo is public.
- Python: **uv**, venv on C: — `$env:UV_PROJECT_ENVIRONMENT = "C:\venvs\ct-brain-anatomy"` before `uv sync`/`uv run` (convention from the user's root CLAUDE.md on Drive, which no longer applies automatically here).
- `pipeline/cache/` (gitignored, ~1.8 GB) holds downloaded sources: Rorden template, nilearn atlases, the Liu arterial atlas (`Atlas_182_MNI152/`, from NITRC behind a free login), the CQ500-CT-148 study + cached registration (`ct_subject/`). Not reproducible from the repo alone — back it up if needed.

## Run
```powershell
npm install
npm run dev        # http://localhost:5173
npm run build      # typecheck + static build to dist/
```
`tools/run.mjs` / `vite.config.mjs` / tsconfig `paths` still support a `DEPS_DIR` outside the project (used while the project was on Google Drive, where npm can't write); with local `node_modules` they behave normally.

Data pipeline:
```powershell
$env:UV_PROJECT_ENVIRONMENT = "C:\venvs\ct-brain-anatomy"
cd pipeline; uv sync; uv run python run_all.py
```
Pipeline outputs are committed to `public/data/` so the site never needs the pipeline at runtime. Run order: build_ct → build_labels → build_overlays → build_territories → build_vessels → build_meshes → check_labels.

## Architecture
- **Vite + TypeScript**, no framework. `src/main.ts` boots: load manifests → sidebar → 2 slice viewers + 3D viewer → apply topic.
- **`src/state.ts`** — the single store (`state`, `setState(patch, source)`, `subscribe`). Panels never talk to each other; they read/write the store. `source` lets a panel ignore its own echo.
- **`src/types.ts`** — data contracts for `public/data/volumes.json` and `public/data/structures.json`.
- **`src/data.ts`** — loads manifests, lookup maps, `displayName()` (adds L/R from MNI x).
- **`src/viewers/sliceViewer.ts`** — NiiVue. `mode: 'base'` = CT (or later MRI) + colour label overlay; `mode: 'stylised'` = label map only, flat colours.
- **`src/viewers/model3d.ts`** — three.js, GLB meshes per structure, slice plane + clipping at the crosshair, explode, picking.
- **`src/ui/sidebar.ts`** — topic tabs, controls, structure list, info card, About/sources dialog.
- **`pipeline/`** — fetch sources → build label volumes → marching-cubes meshes.
- **Vessel slab** (`state.vesselSlab`, sidebar slider 0–10 mm, default ±3): the vessels layer is loaded twice in each slice viewer. The lower copy (`slab: true` in `labelVols`) is filled by `applySlab()` with the nearest vessel label within ±N voxels along the current plane's normal and drawn dim; the original layer on top shows the vessel cut by this slice, brightened. Never change label values in a loaded NiiVue label volume beyond its original range — it breaks the label LUT (whole-volume colour block). In 3-plane layout the slab follows the selected plane's axis only.
- **Notes pane** collapses via the top-bar 'Hide notes' button (`initNotesToggle()`, `body.notes-collapsed`, remembered in localStorage).
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
| CT | CQ500 dataset case CQ500-CT-148 (qure.ai; Chilamkurthy et al., Lancet 2018), via the Academic Torrents copy (only public source). Defaced and warped to MNI. | CC BY-NC-SA 4.0 (derived CT shared alike) |
| Registration target / fallback CT | Rorden Clinical Toolbox `scct.nii` (github.com/neurolabusc/Clinical), Cormack units → HU | BSD |
| Arterial territories | Liu C-F et al. 2023 Arterial Territory Atlas (Sci Data 10:74), NITRC 'arterialatlas' — needs a free NITRC login; user downloads into `pipeline/cache/` | CC BY 4.0 |
| Lobes (48 HO cortical regions → 6 lobes, WM → nearest lobe), insula, deep grey, lateral ventricles | Harvard–Oxford cortical + subcortical (FSL, via nilearn) | FSL licence, non-commercial |
| Cerebellum, midbrain/pons/medulla | Talairach Daemon atlas in MNI space (via nilearn) | free for research/education, attribute |
| Corpus callosum, internal capsule | derived in pipeline | own work |
| Vessels, falx/tentorium, fissures/sulci, CSF spaces | hand-authored / derived in pipeline | own work |
Exact URLs and procedures: `pipeline/README.md`. Public repo + non-commercial licences (CQ500, FSL) ⇒ project is for non-commercial educational use.

## Status / roadmap
- [x] **Phase 1** (2026-11-25) — scaffold, pipeline (CT + 16 anatomy labels + 28 meshes), three linked panels (CT+overlay, stylised slice, 3D with slice plane/clip/explode/picking), topic tabs, info cards, About/sources. Typecheck + build pass, no console errors.
- [x] **Better lobes** (2026-11-25) — lobes rebuilt from 48 Harvard–Oxford cortical regions (`LOBE_MAP` in build_labels.py), white matter → nearest lobe per hemisphere, mode-filtered. Added corpus callosum (50) and internal capsule (51), derived geometrically before the WM fill. Check images: `pipeline/check_labels.py` → `pipeline/cache/check_*.png`.
- [x] **Phase 2** (2026-11-25) — CSF: 3rd/4th ventricles, aqueduct, suprasellar/interpeduncular/ambient/quadrigeminal/prepontine cisterns, cisterna magna (anatomy layer 31–33, 40–45; `add_csf_labels` in build_labels.py, CT HU thresholds inside ROIs + geometric fallbacks). New layers `sulci` (interhemispheric, Sylvian, central, parieto-occipital, calcarine) and `dura` (falx, tentorium with notch) from `build_overlays.py`. Topics: CSF system, Fissures & sulci, Falx & tentorium (topics can set `plane` and `defaultHidden` groups). Check images: `pipeline/cache/check_*.png`, `check_overlay_*.png`.
- [x] **Phase 3** (2026-11-25) — vessels layer: ICA, MCA, ACA, ACom, PCom, PCA, basilar, vertebral from hand-authored centrelines (`pipeline/vessels_mni.yaml`, Opus-owned); SSS, ISS, straight, transverse sinuses derived from the dura labels, sigmoid/deep veins/confluence/cavernous from YAML. `build_vessels.py` rasterises tubes (min 1.5 mm radius) and writes smooth tube GLBs itself (`SELF_MESHED` in build_meshes). Territories layer (ACA/MCA/lenticulostriate/PCA/vertebrobasilar) from `build_territories.py` (Liu 2023 atlas; approximate HO-based fallback if the atlas files are missing). Vessels topic.
- [x] **Real CT** (2026-11-25) — `public/data/ct.nii.gz` is CQ500-CT-148 series 3 (thin plain, 0.625 mm, read normal by all 3 readers; CC BY-NC-SA 4.0), defaced, warped to MNI (antspyx Rigid→Affine→SyN to Rorden template; `build_ct.py`, registration cached in `pipeline/cache/ct_subject/work/`). `--template` restores the old template CT. Labels conformed to it (`real_ct.py`: brain mask from skull >300 HU; lobes clipped/nearest-filled; cerebellum + brainstem rebuilt from CT; CSF thresholds at 20 HU; tentorium height field fitted to the visible tentorium; falx on the real midline). Territories now from the real Liu 2023 atlas (`pipeline/cache/Atlas_182_MNI152/`, x-axis flipped on load). Check images `pipeline/cache/check_fit_*.png`.
- [x] **Phase 4** (2026-11-25) — project moved to `C:\dev\ct-brain-anatomy`; public repo `richardjohnmooney/neuroanatomy-icu`; GitHub Pages live. Note: a push made before Pages is enabled gets cancelled — re-run with `gh workflow run deploy.yml`.
- [ ] Phase 5 — ICU extras: herniation syndromes, raised-ICP CT checklist, "where is the blood?", ASPECTS, EVD/ICP bolt, windowing, quiz.
- [ ] Phase 6 — MRI (MNI152 T1/T2), modality toggle and CT↔MRI compare.

Implementation notes: slice viewer hover tests label layers top-down (last layer in volumes.json first). A missing layer file makes NiiVue fail to load the whole viewer (Vite serves index.html for missing files) — always build all layers listed in volumes.json. NiiVue 0.69 label LUT must use `max` = real max labelValue (not 255) or background takes a label colour; hidden structures = alpha 0 in the LUT. three.js imports use `three/examples/jsm/...` (not `three/addons/...`) so tsconfig `paths` resolve. 3D camera refits to the panel aspect on resize. JS bundle ~1.5 MB (NiiVue) — code-split later if load time matters.

Backlog ideas: lazy-load meshes per topic (public/data is ~24 MB, all meshes load at start — matters on the hosted site), CT↔stylised wipe/curtain slider, outline-only overlay mode, midline-shift measuring tool.

## Known simplifications
- Atlas labels fitted to one real CT are approximate; lobe boundaries follow Harvard–Oxford maxprob (25%) regions.
- Brainstem divisions come from the Talairach atlas, not true landmark segmentation (pons centroid z≈−27); Talairach now only used for cerebellum/brainstem.
- Lobe volumes: frontal 39%, temporal 19%, occipital 18% (a bit large), parietal 17% (a bit small), cingulate 5%, insula 1% — HO extents. Frontal/temporal white-matter boundary is a flat horizontal edge. L/R split by HO hemisphere labels.
- Corpus callosum/internal capsule are geometric approximations: splenium thick, posterior limb of IC thin. 
- Sulci/dura are derived from atlas boundaries, not CT. Central sulcus has a gap at |x|≈18–40 on high axial slices and meets the midline at y≈−20 (real ≈−35). Sylvian fissure has no posterior limb beyond y −30. Tentorium's anterior part is a height field, not a true petrous attachment.
- Arteries are idealised centrelines: they can't be checked against non-contrast CT; MCA/ACA run through fissures that have no CSF label so look 'in parenchyma'. ACom is tiny. Cavernous sinus is a rounded box. Transverse sinuses are symmetric (no dominance).
- Territories (Liu atlas) label names for level-1 values 25–30 were inferred from position (no local label table); anterior choroidal grouped with PCA as the atlas does.
- Quadrigeminal cistern label extends back over the superior vermis to y≈−72 (superior cerebellar cistern region picked up by the HU threshold) — tighten to y > −50 if it bothers users (user said leave for now, 2026-11-25).
- Real-CT fit residuals: third ventricle partly synthetic (~1 mL oversized); interpeduncular cistern tiny, ambient thin; aqueduct geometric; cisterna magna not connected to 4th ventricle; tentorium anterior to y≈−45 is a prior (≤3–4 mm); cerebellum anterolateral edge straight rather than following the petrous ridge; brainstem is a swept ellipse, 2–3 mm off at the CP angle; frontal/temporal/parietal lobe boundaries are atlas flat edges; central sulcus and Sylvian fissure not CT-refined.

# CT Brain anatomy preprocessing pipeline

```
cd pipeline
export UV_PROJECT_ENVIRONMENT='C:\venvs\ct-brain-anatomy'   # PowerShell: $env:UV_PROJECT_ENVIRONMENT=...
uv sync
uv run python run_all.py      # fetch_sources -> build_labels -> build_meshes
```
Outputs go to `../public/data/` (`ct.nii.gz`, `labels_anatomy.nii.gz`, `meshes/*.glb`). Downloads cache in `cache/` (gitignored, treated as data only).

Grid: FSL MNI152 1 mm, 182x218x182, RAS+ affine of the Harvard-Oxford 1 mm atlas (origin -91,-126,-72). Meshes are in MNI world mm, RAS (x=Right, y=Anterior, z=Superior), split L/R at x=0.

Extending: add a `build_<layer>()` function in `build_labels.py` (writes `labels_<layer>.nii.gz` via `save_nifti`, using `resample_to_grid` for any MNI-space source) and call `build_meshes.build_layer(layer, file)`; a new base volume (e.g. MNI152 T1/T2) is a `resample_to_grid(img, order=1)` + `save_nifti`.

## Sources and licences
- CT: Rorden Clinical Toolbox `scct.nii` (github.com/neurolabusc/Clinical), BSD licence. Stored in Cormack units; converted to HU in `build_labels.py` (see docstring). 2 mm native, linearly upsampled.
- Talairach atlas (lobe level) in MNI space, now used only for cerebellum/midbrain/pons/medulla (outside the cerebral mask), via nilearn (Talairach Daemon, Lancaster et al.; research use, cite Lancaster 1997/2000).
- Harvard-Oxford cortical (`cort-maxprob-thr25-1mm`, mapped to lobes 1-6 by name in `build_labels.LOBE_MAP`; cerebral white matter is assigned to the nearest cortical lobe in the same hemisphere) and subcortical (`sub-maxprob-thr25-1mm`: hemisphere, deep nuclei, ventricles, cerebral mask) atlases (FSL), via nilearn; FSL data licence (non-commercial research/education; check before commercial use).

## Derived structures
- Corpus callosum (50): no Talairach label exists, so it is derived (`derive_corpus_callosum`): white matter within ~5-7 mm of the septum-bridged lateral ventricles, |x| <= 18 mm, not below the ventricle roof (allowed to dip further beyond the ventricle footprint, wrapping genu/splenium).
- Internal capsule (51): cerebral voxels (not nucleus/ventricle) within 5 mm of lentiform and 7 mm of caudate/thalamus, z -5..22, per hemisphere.
- Both are applied before the white-matter lobe fill. `check_labels.py` prints sampled MNI points and writes `cache/check_*.png`.

## CSF spaces in `labels_anatomy` (build_labels.py `add_csf_labels`)
Labels 31-33 (3rd ventricle, aqueduct, 4th ventricle) and 40-45 (cisterns) are added after the atlas labels, using the
CT (CSF is dark) inside anatomical ROIs (neighbouring labels + MNI bounds). Thresholds: `CSF_T` 17 HU (strict),
`CSF_T_RELAX` 23 HU (small partial-volumed cisterns). The 2 mm template CT does not resolve the thalamic slit, aqueduct,
or ambient/suprasellar arms, so those use geometric fallbacks (sagittal polygon + half-width, centreline tube, shell around
the midbrain, axial pentagon). The 3rd ventricle/aqueduct overwrite thalamus/midbrain voxels; cisterns overwrite
unlabelled and lobe-fill voxels (quadrigeminal also cerebellum; the interpeduncular fossa is notched into the midbrain).
A median channel (foramen of Magendie) joins the 4th ventricle to the cisterna magna. `build_labels.py` prints per-label
count/median HU/centroid and CSF-path connectivity; `check_labels.py` writes CT+label overlays
(`cache/check_axial_z-10`, `axial_z-30`, `sagittal_x0`, `coronal_y-30`, plus `_ct` variants). `cache/labels_anatomy_base.npy`
is the pre-CSF label volume (dev aid).

## Overlay layers (`build_overlays.py`)
Writes `labels_sulci.nii.gz` and `labels_dura.nii.gz` (uint8, same grid as `labels_anatomy`) plus `cache/check_overlay_*.png` (CT W80/L40, faint lobe colours, overlays in structures.json colours). Run after `build_labels.py`; mesh with `build_meshes.build_layer('sulci'|'dura', file)` (SIGMA 0.6). The CT template has no resolvable sulci/dura, so everything is geometric, built from `labels_anatomy` and Harvard-Oxford cortical regions; bands are 2-3 voxels thick.
- Sulci: 1 interhemispheric = midline voxels (|x|<=1) with cerebral cortex within 6 mm on both sides, excluding callosum/deep grey/brainstem. 2 Sylvian = temporal|frontal+parietal interface (y>-30) plus insula|operculum interface, lateral half of the insula only. 3 central = frontal|parietal lobe interface within 9 mm of both HO precentral and postcentral and <=17 mm below the surface. 4 parieto-occipital = precuneus|cuneus (and parietal|occipital) interface, |x|<16, y<-40. 5 calcarine = mid-surface (middle z per column, 3 voxels) of HO intracalcarine cortex.
- Dura: 1 falx = |x|<=1 sheet inside the inner-skull hull, above a free edge (callosum top +5 mm anteriorly, +2 mm over the body, falling to the crista galli level z=-8 at y~52, and down to the tentorium ridge posteriorly). 2 tentorium = Voronoi interface between cerebellum and cerebral lobes (|dC-dU|<=2 mm, y<-40), plus a forward part (y -41..-6) built from the sheet's height profile descending 0.25 mm/mm towards the front; notch = nothing within 5 mm of midbrain/upper pons.

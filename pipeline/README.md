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

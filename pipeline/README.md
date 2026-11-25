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

## Arterial territories (`build_territories.py`)
Writes `labels_territories.nii.gz` (uint8 on the anatomy grid: 1 ACA, 2 MCA, 3 lenticulostriate, 4 PCA, 5 vertebrobasilar; L/R split is done later from x by `build_meshes.py`) and `cache/check_territories_{z-5,z10,z45,y-10}.png`.
- Source: Liu C-F et al. 2023, "Digital 3D Brain MRI Arterial Territories Atlas", Sci Data 10:74, doi 10.1038/s41597-022-01923-0 (CC BY 4.0; NITRC project `arterialatlas`, https://www.nitrc.org/projects/arterialatlas/). The NITRC download is login-gated, so the FSL MNI152 182x218x182 files were downloaded manually into `cache/Atlas_182_MNI152/` (data only); `fetch_sources.fetch_arterial_atlas()` just detects them. The atlas x axis is flipped relative to our grid (affine x = 90-i vs i-91), so `build_from_atlas` flips axis 0 (shape/y/z affine asserted equal).
- Label table: no label text file is local (`cache/arterial/labels.txt` is an HTML login page). Level-1 labels (odd = left, even = right) were identified from the paper's Fig 8 subregion legend plus the data (level-2 parent, MNI location, overlap with `labels_anatomy`): 1/2 ACA, 3/4 ACA medial lenticulostriate, 5/6 MCA lateral lenticulostriate, 7/8 MCA frontal, 9/10 parietal, 11/12 temporal, 13/14 occipital, 15/16 insula, 17/18 PCA temporal, 19/20 PCA occipital, 21/22 PCA posterior choroidal+thalamoperforating, 23/24 anterior choroidal+thalamoperforating, 25/26 basilar/brainstem, 27/28 superior cerebellar, 29/30 inferior cerebellar, 31/32 ventricles.
- Mapping: ACA -> 1; both lenticulostriate pairs -> 3; MCA cortical -> 2; PCA incl. thalamoperforators -> 4; anterior choroidal -> 4 (the atlas groups it under PCA and it lies posterior/inferior to the lentiform nucleus, in the medial temporal region); SCA/AICA/PICA/basilar -> 5; ventricles dropped. Result is restricted to brain (labels_anatomy > 0, minus CSF labels 30-45); unlabelled brain voxels take the nearest territory in the same hemisphere (cerebellum/brainstem gaps -> 5).
- `build_fallback` (Harvard-Oxford + anatomy approximation) is kept and used only if the atlas files are missing.

## Vessels (`build_vessels.py`)
Reads `vessels_mni.yaml` (hand-authored centrelines, MNI mm RAS; `mirror: true` entries are authored for +x and mirrored to -x), `labels_dura.nii.gz`, `labels_anatomy.nii.gz`, `ct.nii.gz`. Writes `labels_vessels.nii.gz` (uint8; each vessel a tube of radius max(r, 1.5 mm); earlier YAML entries win overlaps, so arteries keep priority over sinuses and the cavernous box is last), the vessel GLBs in `meshes/` (swept-circle tubes along spline-resampled centrelines with parallel-transport frames, true radius, sphere end joints; cavernous sinus = rounded-box SDF via marching cubes; `build_meshes.py` skips this layer), and `cache/check_vessels_*.png` (axial z=-12, sagittal x=0, axial z=-5 and z=-20 posterior, coronal y=0, MIPs coloured by structure incl. arteries-only). Needs `pyyaml`.
- Derived from the dura where the YAML says `derive:` (fallback to the YAML path on any failure): superior sagittal sinus = upper/posterior rim of the falx in sagittal projection (ray casting) pulled 2.5 mm inside, ending at the confluence (lowest posterior falx + 3 mm); straight sinus = top of the tentorium ridge at the midline (+1.5 mm) from the vein of Galen to the confluence; inferior sagittal sinus = lowest falx voxel per y-slice (+1.5 mm), y 30 to -35, then joined to the vein of Galen; transverse sinus = outermost tentorium voxel per y-slice from the confluence to y -62 (mirrored for the left). The sigmoid sinus keeps its YAML path but is shifted so it starts at the derived transverse end (offset tapering to zero at the jugular foramen); the confluence is placed at the derived SSS end.
- The script prints per-artery median CT HU and the percentage of centreline points inside parenchyma labels (lobes 1-6, deep grey 20-24, brainstem 11-13) or CSF labels. The template's Sylvian/interhemispheric CSF is not labelled in `labels_anatomy`, so MCA/ACA read as "parenchyma" by construction; use it for the cistern vessels (ICA terminus, PCom, basilar, P1/P2).

## Real-CT fitting (labels follow the registered real CT)
`run_all.py` runs `build_ct.main()` (writes `public/data/ct.nii.gz` + `cache/ct_source.txt` = real|template) before
`build_labels.main()`, which only *reads* the CT. With the real CT, `real_ct.py` supplies: brain mask (bone > 300 HU skull,
connected soft tissue inside), tentorium height field (prior + bright sheet in the CT), swept-ellipse brainstem,
cerebellum (posterior fossa below the tentorium, behind the petrous ridge), CT-thresholded CSF spaces (T = 20 HU) and the
falx midline line. All brain labels are clipped to the mask and gaps filled from the nearest same-hemisphere label.
Template CT keeps the old atlas-only path. `build_overlays.py` uses the same mask / tentorium / midline.
Check images: `cache/check_fit_*.png`.

## Simulated CTA / CTV (`build_cta.py`)
Writes `public/data/cta.nii.gz` (arterial phase) and `ctv.nii.gz` (venous phase): int16 HU, same header/grid as `ct.nii.gz`, plus `cache/check_cta_*.png`. Runs after `build_vessels.main()` (~10 s) and reuses `build_vessels.prepare_geometry` (final centrelines: arteries, derived sinuses, deep veins, snapped sigmoid/confluence; cavernous sinus = rounded box) with their **true radii**.
- Per vessel: partial-volume fraction f = clip(r - d + 0.5, 0, 1) from the distance to the centreline resampled at 0.2 mm; f and f*HU are blurred (PSF sigma 0.6 mm); HU = CT*(1-F) + blurred f*HU; Gaussian noise (sigma measured from the CT in eroded cerebral parenchyma, ~2.3 HU) x F added only where F > 0.01; voxels with CT > 300 never go below the original; everything else is bit-identical to `ct.nii.gz`.
- Lumen HU, CTA: ICA/basilar/vertebral 380, MCA/ACA/PCA 380 tapering to 330 along each path, ACom/PCom 350, sinuses 140, cavernous 200, deep veins 110. CTV: sinuses and deep veins 300, cavernous 260, arteries 170.

# NeuroAnatomy for ICU

Interactive neuroanatomy for ICU registrars: a real (anonymised, defaced) normal head CT sliced in three planes with colour-coded structures, a stylised atlas slice, and a 3D model — all linked to one crosshair.

Topics: lobes & brainstem, CSF system, fissures & sulci, falx & tentorium, vessels & arterial territories.

**Educational use only — not for clinical decision-making.** Teaching text is a draft pending clinical review.

## Run locally
```
npm install
npm run dev
```
Rebuilding the data needs the Python pipeline in `pipeline/` (see `pipeline/README.md`).

## Data sources & licences
- Head CT: CQ500 dataset, case CQ500-CT-148 (qure.ai; Chilamkurthy et al., Lancet 2018) — CC BY-NC-SA 4.0. The derived, defaced CT in `public/data/ct.nii.gz` is shared under the same licence.
- Arterial territories: Liu C-F et al., Digital 3D Brain MRI Arterial Territories Atlas, Sci Data 2023 — CC BY 4.0.
- Lobes, deep grey nuclei: Harvard–Oxford atlases (FMRIB, Oxford) — FSL licence, non-commercial.
- Cerebellum/brainstem prior: Talairach Daemon atlas in MNI space (Lancaster et al.).
- Registration template: Rorden Clinical Toolbox CT template — BSD.
- Rendering: NiiVue (BSD-2), three.js (MIT).

Because of the non-commercial terms above, this project and its data are for non-commercial educational use.

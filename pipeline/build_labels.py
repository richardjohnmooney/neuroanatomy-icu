"""Build ct.nii.gz and labels_anatomy.nii.gz on the MNI152 1 mm grid.

CT template: Rorden Clinical Toolbox scct.nii (2 mm, MNI space, skull on).
It is stored in "Cormack units" (clinical_ctnorm.m / clinical_cormack.m), NOT HU:
  x = HU - min(=-1024);  t = max(x-900,0)   (kUninterestingDarkUnits = 900)
  c = x + t + 9*min(t,200)                  (kInterestingMidUnits = 200, ratio 10)
Inverse used here (assuming original min = -1024):
  c < 900          : HU = c - 1024
  900 <= c <= 3100 : HU = (c-900)/11 - 124
  c > 3100         : HU = (c-2700)/2 - 124
Brain parenchyma then lands at ~20-40 HU, CSF ~0-10, air -1024. Bone is limited by the 2 mm
template averaging (peaks ~400 HU, still well above brain for bone window).
"""
import numpy as np
import nibabel as nib
from scipy import ndimage as ndi
from nilearn import datasets
from common import CACHE, DATA, GRID_SHAPE, grid_affine, resample_to_grid, save_nifti, structures

ND = str(CACHE / "nilearn")


def cormack_to_hu(c):
    c = c.astype(np.float32)
    return np.where(c < 900, c - 1024,
                    np.where(c <= 3100, (c - 900) / 11.0 - 124, (c - 2700) / 2.0 - 124))


def build_ct():
    img = nib.load(str(CACHE / "scct.nii"))
    c = np.asarray(img.dataobj).astype(np.float32)
    hu_img = nib.Nifti1Image(cormack_to_hu(c), img.affine)
    hu = resample_to_grid(hu_img, order=1, cval=-1024.0)
    hu = np.clip(np.round(hu), -1024, 3071)
    save_nifti(hu, DATA / "ct.nii.gz", np.int16)
    return hu


def tal(level):
    a = datasets.fetch_atlas_talairach(level, data_dir=ND, verbose=0)
    m = a.maps if not isinstance(a.maps, str) else nib.load(a.maps)
    return resample_to_grid(m, order=0, dtype=np.int16), {n: i for i, n in enumerate(a.labels)}


def ho(name):
    a = datasets.fetch_atlas_harvard_oxford(name, data_dir=ND, verbose=0)
    assert a.maps.shape == GRID_SHAPE
    return np.asarray(a.maps.dataobj).astype(np.int16), {n: i for i, n in enumerate(a.labels)}


def majority_filter(lab, classes, size=3):
    """Per-voxel argmax of box-filtered one-hot masks (only changes voxels already labelled)."""
    scores = np.stack([ndi.uniform_filter((lab == c).astype(np.float32), size) for c in classes])
    best = np.array(classes)[scores.argmax(0)]
    return np.where(np.isin(lab, classes), best, lab)


# --- Harvard-Oxford cortical -> lobe mapping (exact HO names; audited/printed at runtime) ---
LOBE_NAMES = {1: "Frontal", 2: "Parietal", 3: "Temporal", 4: "Occipital", 5: "Insula", 6: "Cingulate"}
LOBE_MAP = {
    1: ["Frontal Pole", "Superior Frontal Gyrus", "Middle Frontal Gyrus",
        "Inferior Frontal Gyrus, pars triangularis", "Inferior Frontal Gyrus, pars opercularis",
        "Precentral Gyrus", "Frontal Medial Cortex",
        "Juxtapositional Lobule Cortex (formerly Supplementary Motor Cortex)", "Subcallosal Cortex",
        "Paracingulate Gyrus", "Frontal Orbital Cortex", "Frontal Opercular Cortex",
        "Central Opercular Cortex"],
    2: ["Postcentral Gyrus", "Superior Parietal Lobule", "Supramarginal Gyrus, anterior division",
        "Supramarginal Gyrus, posterior division", "Angular Gyrus", "Precuneous Cortex",
        "Parietal Opercular Cortex"],
    3: ["Temporal Pole", "Superior Temporal Gyrus, anterior division",
        "Superior Temporal Gyrus, posterior division", "Middle Temporal Gyrus, anterior division",
        "Middle Temporal Gyrus, posterior division", "Middle Temporal Gyrus, temporooccipital part",
        "Inferior Temporal Gyrus, anterior division", "Inferior Temporal Gyrus, posterior division",
        "Inferior Temporal Gyrus, temporooccipital part", "Parahippocampal Gyrus, anterior division",
        "Parahippocampal Gyrus, posterior division", "Temporal Fusiform Cortex, anterior division",
        "Temporal Fusiform Cortex, posterior division", "Temporal Occipital Fusiform Cortex",
        "Planum Polare", "Heschl's Gyrus (includes H1 and H2)", "Planum Temporale"],
    4: ["Lateral Occipital Cortex, superior division", "Lateral Occipital Cortex, inferior division",
        "Intracalcarine Cortex", "Cuneal Cortex", "Lingual Gyrus", "Occipital Fusiform Gyrus",
        "Supracalcarine Cortex", "Occipital Pole"],
    5: ["Insular Cortex"],
    6: ["Cingulate Gyrus, anterior division", "Cingulate Gyrus, posterior division"],
}

# tunables for the derived white-matter tracts (mm)
CC_XMAX, CC_THICK, CC_BELOW, CC_WRAP = 18.0, 7.0, 3.0, 1.0
IC_D_LENT, IC_D_CT = 5.0, 7.0


def cortex_to_lobes(cort, C):
    """HO cortical index volume -> lobe label volume (1-6). Fails loudly on unmapped/unknown names."""
    name2lobe = {n: l for l, ns in LOBE_MAP.items() for n in ns}
    assert len(name2lobe) == sum(len(v) for v in LOBE_MAP.values()), "duplicate name in LOBE_MAP"
    unknown = set(name2lobe) - set(C)
    assert not unknown, f"LOBE_MAP names not in HO atlas: {unknown}"
    unmapped = [n for n in C if n != "Background" and n not in name2lobe]
    assert not unmapped, f"HO labels with no lobe: {unmapped}"
    lut = np.zeros(max(C.values()) + 1, np.uint8)
    print("HO cortical -> lobe mapping")
    for n, i in sorted(C.items(), key=lambda kv: kv[1]):
        if n != "Background":
            lut[i] = name2lobe[n]
            print(f"  {i:3d} {n:70s} -> {name2lobe[n]} {LOBE_NAMES[name2lobe[n]]}")
    return lut[cort]


def world_axes():
    A = grid_affine()
    ax = [A[i, i] * np.arange(GRID_SHAPE[i]) + A[i, 3] for i in range(3)]
    return (ax[0][:, None, None].astype(np.float32), ax[1][None, :, None].astype(np.float32),
            ax[2][None, None, :].astype(np.float32))


def derive_corpus_callosum(wm, vent, nuc, X, Y, Z):
    """Arch of white matter hugging the roof of the lateral ventricles (body), wrapping round the
    frontal horns (genu) and atria (splenium)."""
    st = np.ones((9, 1, 1), bool)
    vc = ndi.binary_closing(vent, structure=st) | vent               # bridge the septum pellucidum
    dist = ndi.distance_transform_edt(~vc)
    col = vc.any(2)                                                  # ventricle footprint in (x, y)
    roof = np.where(col, np.where(vc, Z, -999).max(2), 0).astype(np.float32)
    d2, (ix, iy) = ndi.distance_transform_edt(~col, return_indices=True)
    roof_near = roof[ix, iy]                                         # roof height of the nearest ventricle column
    thick = CC_THICK - 2.0 * np.clip(np.abs(X) / CC_XMAX, 0, 1)      # thinner laterally
    # above the ventricle roof; allowed to dip lower the further beyond the ventricle footprint
    # (genu / splenium wrap down round the horns). Excludes fornix, septum, corona radiata below.
    floor = roof_near[:, :, None] - CC_BELOW - CC_WRAP * d2[:, :, None]
    cc = wm & ~vent & ~nuc & (np.abs(X) <= CC_XMAX) & (dist <= thick) & (Z >= floor)
    lab, n = ndi.label(cc)
    if n > 1:
        cc = lab == (np.argmax(np.bincount(lab.ravel())[1:]) + 1)
    return cc


def derive_internal_capsule(allowed, lent, cau_thal, Z):
    dl = ndi.distance_transform_edt(~lent)
    dc = ndi.distance_transform_edt(~cau_thal)
    return allowed & (dl <= IC_D_LENT) & (dc <= IC_D_CT) & (Z >= -5) & (Z <= 22)


def build_anatomy_labels():
    lobe, L = tal("lobe")
    cort, C = ho("cort-maxprob-thr25-1mm")
    sub, S = ho("sub-maxprob-thr25-1mm")
    X, Y, Z = world_axes()
    lobes_ctx = cortex_to_lobes(cort, C)                              # 0..6

    tg = datasets.fetch_atlas_talairach("gyrus", data_dir=ND, verbose=0)
    has_cc = [n for n in tg.labels if "callos" in n.lower() and "sub" not in n.lower()]
    print("Talairach gyrus-level corpus callosum labels:", has_cc or "none -> deriving CC")
    assert not has_cc, "Talairach CC label exists; evaluate it instead of deriving"

    left_ids = [S[n] for n in S if n.startswith("Left ")]
    right_ids = [S[n] for n in S if n.startswith("Right ")]
    hemi = np.where(np.broadcast_to(X, GRID_SHAPE) < 0, 1, 2).astype(np.uint8)
    hemi[np.isin(sub, left_ids)] = 1; hemi[np.isin(sub, right_ids)] = 2

    wm = np.isin(sub, [S["Left Cerebral White Matter"], S["Right Cerebral White Matter"]])
    vent = np.isin(sub, [S["Left Lateral Ventricle"], S["Right Lateral Ventricle"]])
    nucs = {20: ["Left Caudate", "Right Caudate"],
            21: ["Left Putamen", "Right Putamen", "Left Pallidum", "Right Pallidum"],
            22: ["Left Thalamus", "Right Thalamus"], 23: ["Left Hippocampus", "Right Hippocampus"],
            24: ["Left Amygdala", "Right Amygdala"]}
    nucm = {v: np.isin(sub, [S[n] for n in ns]) for v, ns in nucs.items()}
    nuc = np.any(list(nucm.values()), axis=0)

    cer_names = [n for n in S if n != "Background" and n != "Brain-Stem"]
    cerebral = ndi.binary_fill_holes(np.isin(sub, [S[n] for n in cer_names]) | (lobes_ctx > 0))

    # corpus callosum + internal capsule first, so the white-matter lobe fill cannot absorb them
    cc = derive_corpus_callosum(wm, vent, nuc, X, Y, Z)
    ic = np.zeros(GRID_SHAPE, bool)
    for h in (1, 2):
        hm = hemi == h
        ic |= derive_internal_capsule(cerebral & hm & ~cc & ~nuc & ~vent,
                                      nucm[21] & hm, (nucm[20] | nucm[22]) & hm, Z)
    protect = cc | ic

    out = np.zeros(GRID_SHAPE, np.uint8)
    ctx = lobes_ctx.copy(); ctx[protect] = 0
    out[ctx > 0] = ctx[ctx > 0]
    fillable = cerebral & ~nuc & ~vent & ~protect & (out == 0)
    for h in (1, 2):
        hm = hemi == h
        src = np.isin(out, [1, 2, 3, 4, 6]) & hm        # insula cortex keeps its label but isn't a fill source
        _, idx = ndi.distance_transform_edt(~src, return_indices=True)
        nearest = out[tuple(idx)]
        sel = fillable & hm
        out[sel] = nearest[sel]
    out = majority_filter(out, [1, 2, 3, 4, 6]).astype(np.uint8)    # lobes only; labelled set unchanged

    # cerebellum / brainstem from Talairach, only outside the cerebral mask
    pfv = np.zeros(GRID_SHAPE, np.uint8)
    for name, v in {"Anterior Lobe": 10, "Posterior Lobe": 10, "Midbrain": 11, "Pons": 12, "Medulla": 13}.items():
        pfv[lobe == L[name]] = v
    pfv[cerebral] = 0
    pfv = majority_filter(pfv, [10, 11, 12, 13]).astype(np.uint8)
    pfv[cerebral] = 0
    brain = cerebral | (pfv > 0)
    out[pfv > 0] = pfv[pfv > 0]
    out[~brain] = 0

    out[cc] = 50
    out[ic & ~cc] = 51
    for v, m in nucm.items():
        out[m] = v
    out[vent] = 30

    save_nifti(out, DATA / "labels_anatomy.nii.gz", np.uint8)
    return out, brain


def report(out, brain, hu):
    A = grid_affine()
    xs = A[0, 0] * np.arange(GRID_SHAPE[0]) + A[0, 3]
    left = np.broadcast_to((xs < 0)[:, None, None], GRID_SHAPE)
    print(f"{'id':16s}{'val':>4s}{'total':>9s}{'L':>9s}{'R':>9s}")
    for s in structures():
        if s["layer"] != "anatomy":
            continue
        m = out == s["labelValue"]
        assert m.any(), f"label {s['labelValue']} ({s['id']}) missing"
        print(f"{s['id']:16s}{s['labelValue']:4d}{m.sum():9d}{(m & left).sum():9d}{(m & ~left).sum():9d}")
    par = np.isin(out, [1, 2, 3, 4, 6, 10, 20, 21, 22])
    vent = out == 30
    print("brain-mask voxels", int(brain.sum()))
    print("HU in lobes/cerebellum/deep grey: median %.1f IQR %s" %
          (np.median(hu[par]), np.percentile(hu[par], [25, 75]).round(1)))
    print("HU in lateral ventricles: median %.1f IQR %s (n=%d)" %
          (np.median(hu[vent]), np.percentile(hu[vent], [25, 75]).round(1), vent.sum()))
    print("HU thalamus median %.1f; caudate %.1f; whole brain mask median %.1f" %
          (np.median(hu[out == 22]), np.median(hu[out == 20]), np.median(hu[brain])))


def main():
    hu = build_ct()
    out, brain = build_anatomy_labels()
    a = nib.load(str(DATA / "ct.nii.gz")); b = nib.load(str(DATA / "labels_anatomy.nii.gz"))
    assert np.allclose(a.affine, b.affine) and a.shape == b.shape == GRID_SHAPE
    print("affine equal, shape", a.shape)
    report(out, brain, hu)
    print("sizes MB: ct %.2f labels %.2f" % ((DATA / "ct.nii.gz").stat().st_size / 1e6,
                                             (DATA / "labels_anatomy.nii.gz").stat().st_size / 1e6))


if __name__ == "__main__":
    main()

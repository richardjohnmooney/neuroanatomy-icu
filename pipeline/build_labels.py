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


# ---------------------------------------------------------------------------------------------
# CSF spaces (labels 31-33 ventricles, 40-45 cisterns).  CT is the evidence: CSF is dark.  Each space =
# low-HU voxels inside an anatomically defined ROI (neighbouring labels + MNI bounds).  The template CT is
# 2 mm and blurred, so the slit-like spaces (3rd ventricle, aqueduct) use a geometric fallback.
CSF_T = 17.0           # HU threshold for "CSF-like" (brain ~25-35, lateral ventricle median ~2)
CSF_T_RELAX = 23.0     # relaxed threshold for the small, partial-volumed cisterns
# sagittal (y, z) outline of the third ventricle: foramen of Monro -> roof -> pineal recess -> aqueduct
# opening -> floor -> optic / infundibular recesses -> anterior wall
V3_POLY = [(3, 13), (-6, 14), (-18, 12), (-25, 7), (-29, -2), (-26, -5), (-18, -7), (-8, -9), (1, -10), (4, -5)]
V3_HALF_W = 1.9        # mm, half-width at mid-height, narrowing towards roof/floor
# aqueduct centreline (y, z) at x=0 and tube radius (mm); runs through the dorsal midbrain into the 4th ventricle
AQ_PATH = [(-28, -2), (-31, -6), (-35, -12), (-38, -17), (-41, -22)]
AQ_RADIUS = 1.5
MAGENDIE_PATH = [(-44, -38), (-49, -46), (-56, -54), (-61, -60)]   # 4th ventricle floor -> cisterna magna (y, z)
# axial (x, y) outline of the suprasellar cistern at the chiasm level
SUPRA_POLY = [(-2.5, 17), (2.5, 17), (4, 8), (15, 5), (15, 1), (8, -3), (5, -6), (-5, -6), (-8, -3), (-15, 1), (-15, 5), (-4, 8)]


def _in_poly(Y, Z, poly):
    """(y, z) polygon -> boolean array broadcastable against the (x, y, z) grid."""
    from matplotlib.path import Path
    yy, zz = np.meshgrid(Y.ravel(), Z.ravel(), indexing="ij")
    m = Path(poly).contains_points(np.stack([yy.ravel(), zz.ravel()], 1)).reshape(yy.shape)
    return m[None, :, :]


def _in_poly_xy(X, Y, poly):
    """(x, y) polygon -> boolean array broadcastable against the grid (any z)."""
    from matplotlib.path import Path
    xx, yy = np.meshgrid(X.ravel(), Y.ravel(), indexing="ij")
    m = Path(poly).contains_points(np.stack([xx.ravel(), yy.ravel()], 1)).reshape(xx.shape)
    return m[:, :, None]


def _tube(X, Y, Z, path, radius):
    """Union of capsules along a polyline in the (y, z) plane at x=0 (arrays broadcast)."""
    m = np.zeros(np.broadcast(X, Y, Z).shape, bool)
    for (y0, z0), (y1, z1) in zip(path[:-1], path[1:]):
        dy, dz = y1 - y0, z1 - z0
        t = np.clip(((Y - y0) * dy + (Z - z0) * dz) / (dy * dy + dz * dz), 0, 1)
        m |= X ** 2 + (Y - (y0 + t * dy)) ** 2 + (Z - (z0 + t * dz)) ** 2 <= radius ** 2
    return m


def _largest(m):
    lab, n = ndi.label(m, structure=np.ones((3, 3, 3)))
    return m if n <= 1 else lab == (np.argmax(np.bincount(lab.ravel())[1:]) + 1)


def add_csf_labels(out, hu):
    out = out.copy()
    X, Y, Z = world_axes()
    ax = np.abs(X)
    bone = ndi.maximum_filter(hu, size=3) > 60           # keep CSF voxels away from bone / air-bone partial volume
    low = (hu < CSF_T) & (hu > -10) & ~bone
    lowr = (hu < CSF_T_RELAX) & (hu > -10) & ~bone
    cb, mid, pons, med = (out == v for v in (10, 11, 12, 13))
    d_mid = ndi.distance_transform_edt(~mid)
    d_pons = ndi.distance_transform_edt(~pons)
    d_stem = ndi.distance_transform_edt(~(mid | pons | med))

    LOBES = (0, 1, 2, 3, 4, 5, 6)                                    # cisterns may replace unlabelled / lobe-filled voxels

    def put(mask, v, over=LOBES):
        m = mask & np.isin(out, over)
        out[m] = v
        return m

    # --- third ventricle: sagittal outline x slit between the thalami (geometric; thalami abut in the atlas)
    prof = _in_poly(Y, Z, V3_POLY)
    zc = (Z - (-10)) / (14 - (-10))                                  # 0 floor .. 1 roof
    hw = V3_HALF_W * np.clip(1.15 - 1.6 * np.abs(zc - 0.5), 0.35, 1.0)
    v3 = prof & (ax <= hw)
    put(v3, 31, over=(0, 22, 6, 1, 11))
    # --- aqueduct (centreline tube; overwrites midbrain)
    put(_tube(X, Y, Z, AQ_PATH, AQ_RADIUS), 32, over=(0, 11, 12, 22, 10))
    # --- fourth ventricle: low-HU gap between pons/medulla (front) and cerebellum (behind)
    hw4 = np.clip(9.0 - 0.55 * np.abs(Z + 31), 1.5, 9.0)
    r4 = (ax <= hw4) & (Y >= -54) & (Y <= -36) & (Z >= -42) & (Z <= -18)
    put(_largest(r4 & low), 33, over=(0, 10))
    # --- cisterna magna: low HU behind the medulla / below the cerebellum, above the foramen magnum;
    # joined to the 4th ventricle by a median channel (foramen of Magendie) through the unlabelled vallecula
    rm = (ax <= 12) & (Y >= -74) & (Y <= -50) & (Z >= -68) & (Z <= -48)
    put(_largest(rm & lowr), 44, over=(0, 10))
    put(_tube(X, Y, Z, MAGENDIE_PATH, 2.0), 44, over=(0, 10, 13))
    # --- quadrigeminal: dark strip behind the tectum, under the splenium (the atlas cerebellum label
    # overlaps it by a few mm, so cerebellar voxels may be replaced)
    rq = (ax <= 10) & (Y >= -54) & (Y <= -34) & (Z >= -10) & (Z <= 8) & (d_mid <= 12)
    put(_largest(rq & lowr), 42, over=LOBES + (10,))
    # --- prepontine: between clivus and anterior pons
    rp = (ax <= 10) & (Y >= -24) & (Y <= -4) & (Z >= -44) & (Z <= -18) & (d_pons <= 4.5)
    put(_largest(rp & lowr), 43)
    # --- interpeduncular: fossa notched into the front of the midbrain (wedge, apex posterior) plus the
    # low-HU space immediately in front of it
    wedge = (Y >= -17) & (Y <= -4) & (Z >= -17) & (Z <= -4) & (ax <= np.clip(0.4 * (Y + 17), 0, 4.0)) & (d_mid <= 12)
    put(wedge & mid, 45, over=(11,))
    ri = (ax <= 6) & (Y >= -24) & (Y <= -4) & (Z >= -18) & (Z <= -3) & (d_mid <= 3)
    put(ri & lowr, 45)
    # --- suprasellar: pentagon / star around the optic chiasm (anterior interhemispheric arm, Sylvian
    # arms laterally, posterior limbs towards the interpeduncular fossa); geometric, CT-gated
    star = _in_poly_xy(X, Y, SUPRA_POLY) & (Z >= -15) & (Z <= -4)
    put(star & (hu < 30) & ~bone, 40)
    # --- ambient: thin wing (shell) round the lateral midbrain, in front joining the interpeduncular
    # fossa, behind the quadrigeminal cistern
    ra = (ax >= 3) & (Y >= -48) & (Y <= -4) & (Z >= -16) & (Z <= 2) & (d_mid <= 3.0) & (hu < 30)
    put(ra, 41)
    return out


def build_anatomy_labels(hu):
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

    np.save(CACHE / "labels_anatomy_base.npy", out)                  # pre-CSF labels (dev aid for add_csf_labels)
    out = add_csf_labels(out, hu)

    save_nifti(out, DATA / "labels_anatomy.nii.gz", np.uint8)
    return out, brain


def csf_report(out, hu):
    """Voxel count, median HU and MNI centroid per CSF label + CSF-path connectivity."""
    A = grid_affine()
    print(f"{'id':24s}{'val':>4s}{'n':>8s}{'medHU':>7s}{'p75':>6s}   centroid MNI (x,y,z)")
    for s in structures():
        if s["layer"] == "anatomy" and 31 <= s["labelValue"] <= 45:
            m = out == s["labelValue"]
            if not m.any():
                print(f"{s['id']:24s}{s['labelValue']:4d}  MISSING"); continue
            c = nib.affines.apply_affine(A, np.argwhere(m).mean(0))
            print(f"{s['id']:24s}{s['labelValue']:4d}{m.sum():8d}{np.median(hu[m]):7.1f}{np.percentile(hu[m], 75):6.1f}"
                  f"   ({c[0]:6.1f},{c[1]:6.1f},{c[2]:6.1f})")
    for name, vals in (("ventricular path 30-33", [30, 31, 32, 33]),
                       ("path + cisterna magna 30-33,44", [30, 31, 32, 33, 44])):
        lab, n = ndi.label(np.isin(out, vals), structure=np.ones((3, 3, 3)))
        sizes = np.bincount(lab.ravel())[1:]
        print(f"connectivity {name}: {n} component(s), sizes {sorted(sizes.tolist(), reverse=True)[:6]}")
    for v in (40, 41, 42, 43, 45):                                   # cistern contact with the CSF path
        m = out == v
        touch = (ndi.binary_dilation(m, structure=np.ones((3, 3, 3))) & np.isin(out, [31, 32, 33, 40, 41, 42, 43, 44, 45]) & ~m).any()
        print(f"  cistern {v}: touches another CSF space: {bool(touch)}")


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
    csf_report(out, hu)
    print("brain-mask voxels", int(brain.sum()))
    print("HU in lobes/cerebellum/deep grey: median %.1f IQR %s" %
          (np.median(hu[par]), np.percentile(hu[par], [25, 75]).round(1)))
    print("HU in lateral ventricles: median %.1f IQR %s (n=%d)" %
          (np.median(hu[vent]), np.percentile(hu[vent], [25, 75]).round(1), vent.sum()))
    print("HU thalamus median %.1f; caudate %.1f; whole brain mask median %.1f" %
          (np.median(hu[out == 22]), np.median(hu[out == 20]), np.median(hu[brain])))


def main():
    hu = build_ct()
    out, brain = build_anatomy_labels(hu)
    a = nib.load(str(DATA / "ct.nii.gz")); b = nib.load(str(DATA / "labels_anatomy.nii.gz"))
    assert np.allclose(a.affine, b.affine) and a.shape == b.shape == GRID_SHAPE
    print("affine equal, shape", a.shape)
    report(out, brain, hu)
    print("sizes MB: ct %.2f labels %.2f" % ((DATA / "ct.nii.gz").stat().st_size / 1e6,
                                             (DATA / "labels_anatomy.nii.gz").stat().st_size / 1e6))


if __name__ == "__main__":
    main()

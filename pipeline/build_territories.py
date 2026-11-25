"""Build labels_territories.nii.gz (uint8; 1 ACA, 2 MCA, 3 lenticulostriate, 4 PCA, 5 vertebrobasilar).

Preferred source: Liu C-F et al. 2023 Arterial Territories Atlas (Sci Data 10:74, doi 10.1038/s41597-022-01923-0;
NITRC project 'arterialatlas', CC BY), FSL MNI152 182x218x182 version in cache/Atlas_182_MNI152/ (user-downloaded;
x axis is flipped relative to our grid and is handled here). build_from_atlas maps its 32 level-1 labels to our 5
territories (see ATLAS_MAP). If the files are missing, the documented APPROXIMATE FALLBACK (build_fallback, from
Harvard-Oxford cortical regions + labels_anatomy) is used.

Fallback method: HO cortical regions are mapped to territories by name/position (seeds); every other brain voxel
(white matter) takes the territory of its nearest seed in the same hemisphere; deep structures from labels_anatomy
override. Brain = labels_anatomy > 0 excluding CSF labels 30-45 (ventricles/cisterns stay 0).
"""
from pathlib import Path
import numpy as np
import nibabel as nib
from scipy import ndimage as ndi
from nilearn import datasets
from common import CACHE, DATA, GRID_SHAPE, grid_affine, save_nifti
import fetch_sources

NAMES = {1: "ACA", 2: "MCA", 3: "lenticulostriate", 4: "PCA", 5: "vertebrobasilar"}
ACA, MCA, LS, PCA, VB = 1, 2, 3, 4, 5


def world():
    A = grid_affine()
    ax = [A[i, i] * np.arange(GRID_SHAPE[i]) + A[i, 3] for i in range(3)]
    return np.meshgrid(*ax, indexing="ij")


def cortex_territory(n, x, y, z):
    ax = np.abs(x)
    if n in ("Frontal Medial Cortex", "Paracingulate Gyrus", "Subcallosal Cortex",
             "Juxtapositional Lobule Cortex (formerly Supplementary Motor Cortex)",
             "Cingulate Gyrus, anterior division", "Cingulate Gyrus, posterior division"):
        return np.full(x.shape, ACA)
    if n == "Superior Frontal Gyrus":
        return np.where(ax <= 22, ACA, MCA)
    if n in ("Frontal Pole", "Frontal Orbital Cortex"):
        return np.where(ax <= 14, ACA, MCA)
    if n in ("Precentral Gyrus", "Postcentral Gyrus", "Superior Parietal Lobule"):
        return np.where((ax <= 12) & (z > 30), ACA, MCA)       # paracentral / medial strip
    if n == "Precuneous Cortex":
        return np.where(y > -58, ACA, PCA)
    if n in ("Intracalcarine Cortex", "Cuneal Cortex", "Lingual Gyrus", "Occipital Fusiform Gyrus",
             "Supracalcarine Cortex", "Occipital Pole", "Parahippocampal Gyrus, anterior division",
             "Parahippocampal Gyrus, posterior division", "Temporal Fusiform Cortex, anterior division",
             "Temporal Fusiform Cortex, posterior division", "Temporal Occipital Fusiform Cortex",
             "Inferior Temporal Gyrus, temporooccipital part", "Inferior Temporal Gyrus, posterior division",
             "Lateral Occipital Cortex, inferior division"):
        return np.full(x.shape, PCA)
    if n == "Lateral Occipital Cortex, superior division":
        return np.where(ax <= 35, PCA, MCA)
    return np.full(x.shape, MCA)   # lateral frontal/parietal/temporal, insula, opercula


def build_fallback():
    anat = np.asarray(nib.load(str(DATA / "labels_anatomy.nii.gz")).dataobj).astype(np.int16)
    x, y, z = world()
    brain = (anat > 0) & ~((anat >= 30) & (anat <= 45))
    a = datasets.fetch_atlas_harvard_oxford("cort-maxprob-thr25-1mm", data_dir=str(CACHE / "nilearn"), verbose=0)
    cort = np.asarray(a.maps.dataobj).astype(np.int16)
    seeds = np.zeros(GRID_SHAPE, np.uint8)
    for i, n in enumerate(a.labels):
        if i == 0 or n == "Background":
            continue
        m = (cort == i) & brain
        if m.any():
            seeds[m] = cortex_territory(n, x, y, z)[m]
    deep = np.zeros(GRID_SHAPE, np.uint8)
    deep[np.isin(anat, [20, 21, 51])] = LS                  # caudate, lentiform, internal capsule
    deep[np.isin(anat, [22, 23, 24])] = PCA                 # thalamus, hippocampus, amygdala (ant. choroidal -> PCA bucket)
    deep[np.isin(anat, [10, 11, 12, 13])] = VB              # cerebellum + brainstem
    cc = anat == 50
    deep[cc & (y > -35)] = ACA
    deep[cc & (y <= -35)] = PCA                              # splenium
    seeds[deep > 0] = deep[deep > 0]
    out = np.zeros(GRID_SHAPE, np.uint8)
    for side in (x < 0, x >= 0):
        s = (seeds > 0) & side
        idx = ndi.distance_transform_edt(~s, return_distances=False, return_indices=True)
        filled = seeds[tuple(idx)]
        m = brain & side
        out[m] = filled[m]
    out[deep > 0] = deep[deep > 0]
    # white matter beyond 25 mm lateral is not ACA: re-fill those voxels from non-ACA seeds
    bad = (out == ACA) & (np.abs(x) > 25)
    for side in (x < 0, x >= 0):
        s = (seeds > 0) & (seeds != ACA) & side
        idx = ndi.distance_transform_edt(~s, return_distances=False, return_indices=True)
        m = bad & side
        out[m] = seeds[tuple(idx)][m]
    return out


# Level-1 label table. The label text file was not available locally (cache/arterial/labels.txt is an HTML login
# page), so names were taken from the paper's Fig 8 subregion legend and the order/side verified from the data:
# odd = left, even = right (MNI x), level-2 parent from ArterialAtlas_level2 (1/2 ACA, 3/4 MCA, 5/6 PCA, 7/8 VB,
# 9/10 ventricles), and each label's location/overlap with labels_anatomy.
ATLAS_MAP = {  # level-1 pair (L,R) -> (name, our territory)
    (1, 2): ("ACA (cortical/frontal-medial)", ACA),
    (3, 4): ("ACA medial lenticulostriate (callosal/Heubner region)", LS),
    (5, 6): ("MCA lateral lenticulostriate", LS),
    (7, 8): ("MCA frontal", MCA),
    (9, 10): ("MCA parietal", MCA),
    (11, 12): ("MCA temporal", MCA),
    (13, 14): ("MCA occipital", MCA),
    (15, 16): ("MCA insula", MCA),
    (17, 18): ("PCA temporal", PCA),
    (19, 20): ("PCA occipital", PCA),
    (21, 22): ("PCA posterior choroidal + thalamoperforating", PCA),
    (23, 24): ("anterior choroidal + thalamoperforating (atlas parent: PCA)", PCA),
    (25, 26): ("basilar / brainstem perforators", VB),
    (27, 28): ("superior cerebellar (SCA)", VB),
    (29, 30): ("inferior cerebellar (AICA/PICA)", VB),
    (31, 32): ("ventricles (excluded)", 0),
}


def build_from_atlas(d):
    anat = np.asarray(nib.load(str(DATA / "labels_anatomy.nii.gz")).dataobj).astype(np.int16)
    img = nib.load(str(Path(d) / "ArterialAtlas.nii"))
    # atlas affine: x = 90 - i; ours: x = i - 91  ->  flip axis 0 (verified: same shape, same y/z affine)
    A = img.affine
    assert img.shape[:3] == GRID_SHAPE and np.allclose(A[1:3, 1:3], grid_affine()[1:3, 1:3])         and np.isclose(A[0, 0], -1) and np.isclose(A[0, 3], 90) and np.isclose(grid_affine()[0, 3], -91), "grid mismatch"
    at = np.rint(np.asarray(img.dataobj)[..., 0] if img.ndim == 4 else np.asarray(img.dataobj)).astype(np.int16)[::-1]
    lut = np.zeros(33, np.uint8)
    print("level-1 label -> territory mapping:")
    for (l, r), (nm, t) in ATLAS_MAP.items():
        lut[l] = lut[r] = t
        print(f"  {l:2d}/{r:2d}  {nm:58s} -> {t} {NAMES.get(t, '-')}")
    seeds = lut[np.clip(at, 0, 32)]
    x, y, z = world()
    brain = (anat > 0) & ~((anat >= 30) & (anat <= 45))
    seeds[~brain] = 0
    print("brain voxels without atlas territory (filled):", int((brain & (seeds == 0)).sum()))
    gap_vb = brain & (seeds == 0) & np.isin(anat, [10, 11, 12, 13])   # cerebellum/brainstem gaps are vertebrobasilar
    seeds[gap_vb] = VB
    out = seeds.copy()
    for side in (x < 0, x >= 0):
        s = (seeds > 0) & side
        idx = ndi.distance_transform_edt(~s, return_distances=False, return_indices=True)
        m = brain & side & (seeds == 0)
        out[m] = seeds[tuple(idx)][m]
    return out


def checks(out):
    x, y, z = world()
    A = np.linalg.inv(grid_affine())
    print("territory voxel counts (L = x<0, R = x>=0):")
    for k, n in NAMES.items():
        print(f"  {k} {n:16s} L {int(((out == k) & (x < 0)).sum()):8d}  R {int(((out == k) & (x >= 0)).sum()):8d}")
    pts = {"MCA (+-40,10,30)": ((40, 10, 30), 2), "ACA (+-5,20,45)": ((5, 20, 45), 1),
           "PCA (+-10,-85,5)": ((10, -85, 5), 4), "LS (+-20,0,5)": ((20, 0, 5), 3),
           "VB (+-20,-60,-30)": ((20, -60, -30), 5), "MCA (+-45,-20,0)": ((45, -20, 0), 2)}
    for nm, ((px, py, pz), exp) in pts.items():
        r = []
        for sx in (-1, 1):
            i, j, k = np.round(A @ [sx * px, py, pz, 1])[:3].astype(int)
            r.append(int(out[i, j, k]))
        print(f"  {nm}: L={r[0]} R={r[1]} expected {exp} {'OK' if r == [exp, exp] else 'MISMATCH'}")


def check_images(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    ct = np.asarray(nib.load(str(DATA / "ct.nii.gz")).dataobj).astype(np.float32)
    A = np.linalg.inv(grid_affine())
    cm = ListedColormap(["#000000", "#90be6d", "#f9c74f", "#f94144", "#f8961e", "#577590"])
    for ax_, v in (("z", -5), ("z", 10), ("z", 45), ("y", -10)):
        p = np.round(A @ ([0, 0, v, 1] if ax_ == "z" else [0, v, 0, 1])).astype(int)
        sl = (slice(None), slice(None), p[2]) if ax_ == "z" else (slice(None), p[1], slice(None))
        c, t = ct[sl].T, out[sl].T
        fig, a = plt.subplots(figsize=(6, 6))
        a.imshow(c, cmap="gray", vmin=0, vmax=80, origin="lower")
        a.imshow(np.ma.masked_equal(t, 0), cmap=cm, vmin=0, vmax=5, alpha=0.6, origin="lower", interpolation="nearest")
        a.set_title(f"territories {ax_}={v} (image left = patient left)"); a.axis("off")
        fig.savefig(CACHE / f"check_territories_{ax_}{v}.png", dpi=80, bbox_inches="tight"); plt.close(fig)


def main():
    d = fetch_sources.fetch_arterial_atlas()
    if d is not None:
        out = build_from_atlas(d)
    else:
        print("Using APPROXIMATE fallback (Harvard-Oxford + anatomy labels)")
        out = build_fallback()
    save_nifti(out, DATA / "labels_territories.nii.gz", np.uint8)
    checks(out); check_images(out)


if __name__ == "__main__":
    main()

"""Build labels_sulci.nii.gz and labels_dura.nii.gz on the same grid as labels_anatomy.nii.gz.

Both are geometric constructions (the template CT is a smooth 2 mm average with no resolvable
sulci or dura), derived from labels_anatomy (lobes, cerebellum, brainstem, corpus callosum) and the
Harvard-Oxford cortical atlas, all in MNI RAS mm. Every structure is a band >= 2 voxels thick so
that it survives the Gaussian + marching-cubes meshing in build_meshes.py.

  sulci: 1 interhemispheric  2 Sylvian  3 central  4 parieto-occipital  5 calcarine
  dura : 1 falx cerebri      2 tentorium cerebelli
"""
import numpy as np
import nibabel as nib
from scipy import ndimage as ndi
from nilearn import datasets
from common import CACHE, DATA, GRID_SHAPE, grid_affine, save_nifti, structures

ND = str(CACHE / "nilearn")
AFF = grid_affine()
AX = [AFF[i, i] * np.arange(GRID_SHAPE[i]) + AFF[i, 3] for i in range(3)]
X = AX[0][:, None, None].astype(np.float32)
Y = AX[1][None, :, None].astype(np.float32)
Z = AX[2][None, None, :].astype(np.float32)
XI = lambda x: int(round((x - AFF[0, 3]) / AFF[0, 0]))
YI = lambda y: int(round((y - AFF[1, 3]) / AFF[1, 1]))
ZI = lambda z: int(round((z - AFF[2, 3]) / AFF[2, 2]))


# ----------------------------------------------------------------------------- helpers
def edt(mask):
    """Distance (mm) from every voxel to the nearest True voxel of mask."""
    return ndi.distance_transform_edt(~mask).astype(np.float32)


def remove_small(mask, min_vox):
    cl, n = ndi.label(mask)
    if n == 0:
        return mask
    sz = np.bincount(cl.ravel()); sz[0] = 0
    return np.isin(cl, np.nonzero(sz >= min_vox)[0])


def lateralise(mask):
    return mask & (X < 0), mask & (X >= 0)


def smooth_profile(v, valid, sigma=3.0):
    """Fill gaps of a 1-D profile by interpolation, then Gaussian-smooth."""
    idx = np.arange(len(v))
    v = np.interp(idx, idx[valid], v[valid])
    return ndi.gaussian_filter1d(v, sigma, mode="nearest")


# ----------------------------------------------------------------------------- inputs
def load_inputs():
    lab = np.asarray(nib.load(str(DATA / "labels_anatomy.nii.gz")).dataobj).astype(np.uint8)
    ct = np.asarray(nib.load(str(DATA / "ct.nii.gz")).dataobj).astype(np.float32)
    a = datasets.fetch_atlas_harvard_oxford("cort-maxprob-thr25-1mm", data_dir=ND, verbose=0)
    cort = np.asarray(a.maps.dataobj).astype(np.int16)
    C = {n: i for i, n in enumerate(a.labels)}
    return lab, ct, cort, C


def intracranial(lab, ct):
    """Soft-tissue space inside the inner table (no CSF resolution in this CT, so used as a hull)."""
    soft = (ct > -20) & (ct < 120)
    core = ndi.binary_erosion(soft, iterations=3)
    cl, _ = ndi.label(core)
    ids = np.bincount(cl[lab > 0]); ids[0] = 0
    core = cl == ids.argmax()
    ic = ndi.binary_fill_holes(ndi.binary_dilation(core, iterations=3) & soft)
    # keep only the neighbourhood of the brain (the skull base / orbits leak a little)
    near = ndi.binary_dilation(lab > 0, iterations=7)
    return ic & near


# ----------------------------------------------------------------------------- sulci
def build_sulci(lab, ct, cort, C, ic):
    cer = np.isin(lab, [1, 2, 3, 4, 5, 6])
    out = np.zeros(lab.shape, np.uint8)
    R = lambda *names: np.isin(cort, [C[n] for n in names])

    # 1 interhemispheric fissure: midline voxels (|x|<=1) that face cerebral cortex on both sides,
    # above the corpus callosum and between the hemispheres anteriorly/posteriorly.
    left = cer & (X < 0); right = cer & (X >= 0)
    near_l = edt(left) <= 6; near_r = edt(right) <= 6
    notcer = ~np.isin(lab, [10, 11, 12, 13, 20, 21, 22, 23, 24, 30, 31, 32, 33, 50, 51])
    ihf = (np.abs(X) <= 1) & near_l & near_r & notcer & ic
    ihf |= (np.abs(X) <= 1) & (Z > 0) & ic & notcer & (Y > -105) & (Y < 75) & \
        ndi.binary_dilation(ihf, iterations=3) & (lab != 50)
    ihf = remove_small(ihf, 500)
    out[ihf] = 1

    # depth below the cortical surface of each hemisphere (mm; the medial surface counts as surface)
    depth = ndi.distance_transform_edt(left) + ndi.distance_transform_edt(right)

    # 2 Sylvian fissure: temporal / (frontal+parietal) interface plus insula / operculum interface,
    # lateral half of the insula only (the circular sulcus facing the opercula)
    ins = lab == 5
    xm = np.full(lab.shape[1], np.nan)
    for j in range(lab.shape[1]):
        if ins[:, j, :].any():
            xm[j] = np.abs(np.broadcast_to(X, lab.shape)[:, j, :][ins[:, j, :]]).mean()
    ok = ~np.isnan(xm)
    xm = np.interp(np.arange(len(xm)), np.nonzero(ok)[0], xm[ok])
    lateral = np.abs(X) > xm[None, :, None] - 1
    d_t = edt(lab == 3); d_fp = edt(np.isin(lab, [1, 2])); d_i = edt(ins)
    d_o = edt(np.isin(lab, [1, 2, 3]))
    syl = ((d_t <= 1.5) & (d_fp <= 1.5) & (Y > -30)) | ((d_i <= 2.0) & (d_o <= 2.0) & ~ins)
    syl &= lateral & (Z > -30) & (Z < 35) & (depth <= 40)
    syl = remove_small(syl, 300)
    out[syl & (out == 0)] = 2

    # 3 central sulcus: frontal / parietal interface in the pre/postcentral region, cortex-deep only
    d1 = edt(lab == 1); d2 = edt(lab == 2); d4 = edt(lab == 4)
    d7 = edt(R("Precentral Gyrus")); d17 = edt(R("Postcentral Gyrus"))
    cs = (d1 <= 2.0) & (d2 <= 2.0) & (d7 <= 9) & (d17 <= 9) & (depth <= 17) & (Z > 8)
    cs = remove_small(cs, 150)
    out[cs & (out == 0)] = 3

    # 4 parieto-occipital sulcus: precuneus / cuneus (and parietal / occipital) interface, medial surface
    d31 = edt(R("Precuneous Cortex")); d32 = edt(R("Cuneal Cortex"))
    pos = (((d31 <= 2.0) & (d32 <= 2.0)) | ((d2 <= 1.5) & (d4 <= 1.5) & (depth <= 12)))         & (np.abs(X) < 16) & (Y < -40) & (Z > 0)
    pos = remove_small(pos, 100)
    out[pos & (out == 0)] = 4

    # 5 calcarine sulcus: mid-surface of intracalcarine cortex (middle z of each (x, y) column, 3 thick)
    ic24 = R("Intracalcarine Cortex") & (np.abs(X) < 20)
    zmin = np.where(ic24.any(2), np.where(ic24, Z, 1e3).min(2), np.nan)
    zmax = np.where(ic24.any(2), np.where(ic24, Z, -1e3).max(2), np.nan)
    zmid = ((zmin + zmax) / 2)[:, :, None]
    cal = ic24.any(2)[:, :, None] & (np.abs(Z - zmid) <= 1.0)
    cal = remove_small(cal, 50)
    out[cal & (out == 0)] = 5
    return out


# ----------------------------------------------------------------------------- dura
def build_dura(lab, ct, ic):
    out = np.zeros(lab.shape, np.uint8)
    cer = np.isin(lab, [1, 2, 3, 4, 5, 6])
    cb = lab == 10
    brainstem = np.isin(lab, [11, 12, 13])

    # ---- tentorium: Voronoi interface between cerebellum and cerebrum (height field z(x,y)),
    # extended to the inner skull and forwards towards the petrous apex
    dC = edt(cb); dU = edt(cer)
    tent = (np.abs(dC - dU) <= 2.0) & ic & (Y < -5) & (Y > -110) & (Z > -45) & (dC < 30)
    tent = remove_small(tent, 500)
    tent &= Y < -40
    # Forward part (y -40 .. -8), where the cerebellum is too low to define the interface: reuse the
    # height profile z(x) of the sheet at y -60..-45 and let it descend gently towards the front
    # (free edge at the clinoids / petrous apex is lower than the straight-sinus apex).
    ref = tent & (Y >= -47) & (Y <= -41)
    zr = np.where(ref.any((1, 2)), (ref * Z).sum((1, 2)) / np.maximum(ref.sum((1, 2)), 1), np.nan)
    okx = ~np.isnan(zr)
    zr = np.interp(np.arange(len(zr)), np.nonzero(okx)[0], zr[okx])
    zr = ndi.gaussian_filter1d(zr, 2.0, mode="nearest")
    zf = zr[:, None] - 0.25 * np.clip(-44 - Y[0, :, 0], 0, None)[None, :]       # (x, y) height field
    inx = (np.abs(AX[0]) <= 58)[:, None] & (AX[1] >= -41)[None, :] & (AX[1] <= -6)[None, :]
    for dz in (-1.0, 0.0, 1.0):
        tent |= (np.abs(Z - (zf[:, :, None] + dz)) < 0.5) & inx[:, :, None] & ic
    # tentorial notch: nothing within 6 mm of midbrain / upper pons
    d_ms = edt((lab == 11) | ((lab == 12) & (Z > -25)))
    tent &= d_ms > 5
    tent = remove_small(tent, 500)

    # ---- falx
    cc = lab == 50
    ys = np.arange(lab.shape[1])
    cctop = np.full(len(ys), np.nan); ccy = np.zeros(len(ys), bool)
    for j in ys:
        sl = cc[:, j, :]
        if sl.any():
            cctop[j] = AX[2][np.nonzero(sl.any(0))[0].max()]; ccy[j] = True
    top = smooth_profile(cctop, ccy, 2.0)
    yw = AX[1]
    ycc = yw[ccy]
    y_gen, y_spl = ycc.max(), ycc.min()
    # tentorium midline ridge -> straight sinus line
    mid = tent & (np.abs(X) <= 3)
    ridge = np.full(len(ys), np.nan); rv = np.zeros(len(ys), bool)
    for j in ys:
        if mid[:, j, :].any():
            ridge[j] = AX[2][np.nonzero(mid[:, j, :].any(0))[0].max()]; rv[j] = True
    ridge = smooth_profile(ridge, rv & (yw < -40), 4.0)
    free = np.empty(len(ys))
    for j, y in enumerate(yw):
        if y >= y_gen - 8:                       # anterior: from above the genu down to the crista galli
            t = np.clip((y - (y_gen - 8)) / (52 - (y_gen - 8)), 0, 1)
            free[j] = (top[j] + 5) * (1 - t) + (-8) * t if y <= 52 else -8
        elif y >= y_spl:                         # over the body: a few mm above the callosum, closer posteriorly
            t = (y - y_spl) / (y_gen - 8 - y_spl)
            free[j] = top[j] + 2 + 3 * t
        else:                                    # posterior: down to the straight sinus line
            free[j] = ridge[j]
    # blend the callosal and straight-sinus parts around the splenium
    free = ndi.gaussian_filter1d(free, 2.0, mode="nearest")
    falx = (np.abs(X) <= 1) & ic & (Z >= free[None, :, None]) & (Y > -108) & (Y < 78)
    falx = remove_small(falx, 500)
    out[tent] = 2
    out[falx] = 1
    return out, free


# ----------------------------------------------------------------------------- check images
def _png(path, slc, lab, ct, sul, dur, ref, scale=4, crop=None):
    """Axial/coronal/sagittal slice: CT W80 L40 + faint lobe colours + sulci/dura overlays."""
    S = {s["layer"] + str(s["labelValue"]): s["color"] for s in structures()}
    hexrgb = lambda h: np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    g = np.clip((ct[slc] - 0) / 80.0, 0, 1)
    rgb = np.repeat(g[..., None], 3, -1) * 255
    L = lab[slc]
    for v in range(1, 7):
        c = S.get("anatomy%d" % v)
        if c:
            m = L == v
            rgb[m] = 0.7 * rgb[m] + 0.3 * hexrgb(c)
    for arr, layer in ((dur, "dura"), (sul, "sulci")):
        A = arr[slc]
        for v in np.unique(A[A > 0]):
            m = A == v
            rgb[m] = hexrgb(S[layer + str(v)]) if layer == "dura" else                 np.where((dur[slc] > 0)[m][:, None], 0.5 * rgb[m] + 0.5 * hexrgb(S[layer + str(v)]),
                         hexrgb(S[layer + str(v)]))
    img = np.transpose(rgb, (1, 0, 2))[::-1]           # horizontal = first axis, up = second axis
    if crop:
        (a0, a1), (b0, b1) = crop                      # in voxel index of the two slice axes
        img = img[img.shape[0] - b1: img.shape[0] - b0, a0:a1]
    img = np.kron(img, np.ones((scale, scale, 1)))
    import matplotlib.image as mi
    mi.imsave(str(path), np.clip(img, 0, 255).astype(np.uint8))


def check_images(lab, ct, sul, dur):
    i = lambda x: XI(x); j = lambda y: YI(y); k = lambda z: ZI(z)
    F = slice(None)
    views = {
        "axial_z50": ((F, F, k(50)), ((20, 162), (30, 190))),
        "axial_z5": ((F, F, k(5)), ((20, 162), (20, 195))),
        "sagittal_x0": ((i(0), F, F), ((10, 205), (20, 175))),
        "sagittal_x3": ((i(3), F, F), ((10, 205), (20, 175))),
        "coronal_y-60": ((F, j(-60), F), ((15, 168), (15, 170))),
        "coronal_y-20": ((F, j(-20), F), ((15, 168), (15, 170))),
        "sagittal_x25": ((i(25), F, F), ((10, 205), (20, 175))),
    }
    for name, (slc, crop) in views.items():
        _png(CACHE / f"check_overlay_{name}.png", slc, lab, ct, sul, dur, None, crop=crop)


# ----------------------------------------------------------------------------- main
def main():
    lab, ct, cort, C = load_inputs()
    ic = intracranial(lab, ct)
    sul = build_sulci(lab, ct, cort, C, ic)
    dur, _ = build_dura(lab, ct, ic)
    for name, arr, layer in (("labels_sulci.nii.gz", sul, "sulci"), ("labels_dura.nii.gz", dur, "dura")):
        save_nifti(arr, DATA / name, np.uint8)
        print(name, {s["id"]: int((arr == s["labelValue"]).sum())
                     for s in structures() if s["layer"] == layer})
    check_images(lab, ct, sul, dur)


if __name__ == "__main__":
    main()

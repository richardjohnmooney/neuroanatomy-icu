"""Simulated contrast-enhanced head CT: CTA (arterial phase) and CTV (venous phase).

Inputs : public/data/ct.nii.gz (real non-contrast CT), vessel centrelines + true radii from
         build_vessels.prepare_geometry (vessels_mni.yaml + sinuses derived from labels_dura).
Outputs: public/data/cta.nii.gz, public/data/ctv.nii.gz (int16 HU, same grid/affine as ct.nii.gz),
         cache/check_cta_*.png.
Method : per vessel, partial-volume fraction f = clip(r - d + 0.5, 0, 1) from the distance d to a dense
         resampling of the centreline (true radius), lumen HU per phase (arteries taper along each path),
         PSF blur (sigma 0.6 mm) of the f and f*HU fields, HU = CT*(1-F) + blur(f*HU), CT-matched Gaussian
         noise (weighted by F) only where F > 0.01, bone (CT > 300) never lowered.
"""
import numpy as np
import nibabel as nib
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from common import CACHE, DATA
import build_vessels as bv

PSF_SIGMA = 0.6
STEP = 0.2

ART_BIG = ("internal-carotid-artery", "basilar-artery", "vertebral-artery")
ART_TAPER = ("middle-cerebral-artery", "anterior-cerebral-artery", "posterior-cerebral-artery")
ART_SMALL = ("anterior-communicating-artery", "posterior-communicating-artery")
# (start HU, end HU along the path) per class, per phase
PHASES = {
    "cta": dict(big=(380, 380), taper=(380, 330), small=(350, 350), sinus=140, deep=110, cav=200),
    "ctv": dict(big=(170, 170), taper=(170, 170), small=(170, 170), sinus=300, deep=300, cav=260),
}


def lumen(vid, frac, ph):
    """Lumen HU of vessel vid at path fraction frac (0 start .. 1 end)."""
    if vid in ART_BIG:
        a, b = ph["big"]
    elif vid in ART_TAPER:
        a, b = ph["taper"]
    elif vid in ART_SMALL:
        a, b = ph["small"]
    elif vid == "deep-cerebral-veins":
        a = b = ph["deep"]
    elif vid == "cavernous-sinus":
        a = b = ph["cav"]
    else:
        a = b = ph["sinus"]
    return a + (b - a) * frac


def measure_noise(ct, anat):
    """Median local SD (3x3x3) in eroded cerebral parenchyma with HU 20..45."""
    m = ndi.binary_erosion(np.isin(anat, [1, 2, 3, 4, 5, 6]), iterations=4) & (ct > 20) & (ct < 45)
    mu = ndi.uniform_filter(ct, 3); mu2 = ndi.uniform_filter(ct * ct, 3)
    sd = np.sqrt(np.maximum(mu2 - mu * mu, 0) * 27 / 26)
    return float(np.median(sd[m])), int(m.sum())


def vessel_items(G, dura):
    """List of (vid, centreline paths in world mm or None for the box, radius, def, side sign)."""
    vessels, vd, _ = bv.prepare_geometry(G, dura, bv.load_vessel_defs())
    out = []
    for d, _ in vessels:
        vid = d["id"]; r = float(d.get("radius", 0))
        for sgn in ([1, -1] if d.get("mirror") else [1]):
            if "box" in d:
                out.append((vid, None, r, d, sgn))
            else:
                out.append((vid, [p * np.array([sgn, 1, 1]) for p in vd[vid]], r, d, sgn))
    return out


def _merge(F0, A0, f, a):
    """Combine vessel fields where they overlap: fraction = max, HU = fraction-weighted mean."""
    Fn = np.maximum(F0, f)
    tot = F0 + f
    An = np.where(tot > 0, (A0 + a) / np.maximum(tot, 1e-6) * Fn, 0)
    return Fn, An.astype(np.float32)


def phase_fields(G, items, ph):
    """Return (F, A): partial-volume fraction and f*HU field (before PSF blur)."""
    shape = G.shape
    F = np.zeros(shape, np.float32); A = np.zeros(shape, np.float32)
    for vid, paths, r, d, sgn in items:
        if paths is None:                                     # cavernous box with a 1-voxel soft edge
            box = d["box"]
            xs = np.array(box["x"]) * sgn
            lo = np.array([xs.min(), box["y"][0], box["z"][0]], float)
            hi = np.array([xs.max(), box["y"][1], box["z"][1]], float)
            v0 = nib.affines.apply_affine(G.inv, lo); v1 = nib.affines.apply_affine(G.inv, hi)
            a0 = np.minimum(v0, v1); a1 = np.maximum(v0, v1)
            sl = tuple(slice(max(int(np.floor(a0[k])) - 3, 0), min(int(np.ceil(a1[k])) + 4, shape[k])) for k in range(3))
            ii = np.stack(np.meshgrid(*[np.arange(s.start, s.stop) for s in sl], indexing="ij"), -1)
            w = nib.affines.apply_affine(G.A, ii.reshape(-1, 3)).reshape(ii.shape)
            rad = 3.0                                          # rounded-box signed distance (matches the mesh)
            q = np.maximum(lo - w, w - hi) + rad
            sd = np.linalg.norm(np.maximum(q, 0), axis=-1) + np.minimum(q.max(-1), 0) - rad
            f = np.clip(0.5 - sd, 0, 1).astype(np.float32)
            F[sl], A[sl] = _merge(F[sl], A[sl], f, f * lumen(vid, 0, ph))
            continue
        for p in paths:
            P = bv.resample(p, step=STEP)
            s = bv.arclen(P)
            frac = s / max(s[-1], 1e-6)
            hu = np.array([lumen(vid, t, ph) for t in frac], np.float32)
            vox = nib.affines.apply_affine(G.inv, P)
            R = r + 2.0
            lo = np.maximum(np.floor(vox.min(0) - R).astype(int), 0)
            hi = np.minimum(np.ceil(vox.max(0) + R).astype(int) + 1, shape)
            ii = np.stack(np.meshgrid(*[np.arange(lo[k], hi[k]) for k in range(3)], indexing="ij"), -1).reshape(-1, 3)
            dist, ix = cKDTree(vox).query(ii, distance_upper_bound=r + 1.0)
            ok = np.isfinite(dist)
            f = np.clip(r - dist[ok] + 0.5, 0, 1).astype(np.float32)
            c = ii[ok]
            reg = (c[:, 0], c[:, 1], c[:, 2])
            F[reg], A[reg] = _merge(F[reg], A[reg], f, f * hu[ix[ok]])
    return F, A


def simulate(ct, F, A, sigma, rng):
    Fb = ndi.gaussian_filter(F, PSF_SIGMA)
    Ab = ndi.gaussian_filter(A, PSF_SIGMA)
    out = ct * (1 - Fb) + Ab
    m = Fb > 0.01
    out[m] += rng.normal(0, sigma, int(m.sum())).astype(np.float32) * Fb[m]
    bone = ct > 300
    out[bone] = np.maximum(ct[bone], out[bone])
    out = np.where(m | bone, out, ct)                         # exactly the original elsewhere
    return np.clip(np.round(out), -1024, 3071).astype(np.int16), Fb


def mip(vol, axis, lo, hi):
    s = [slice(None)] * 3
    s[axis] = slice(lo, hi + 1)
    return vol[tuple(s)].max(axis=axis)


def render_checks(G, cta, ctv):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    A = G.A
    k = lambda x, a: int(round((x - A[a, 3]) / A[a, a]))      # world mm -> voxel index

    def win(img, W, L):
        return np.clip((img - (L - W / 2)) / W, 0, 1)

    def save(img, name):                                      # img[a, b]: a -> horizontal, b -> vertical (up)
        fig = plt.figure(figsize=(8, 8 * img.shape[1] / img.shape[0]), dpi=100)
        plt.axes([0, 0, 1, 1]); plt.imshow(img.T, cmap="gray", origin="lower", vmin=0, vmax=1); plt.axis("off")
        fig.savefig(str(CACHE / name), dpi=100); plt.close(fig)

    z = k(-12, 2)
    save(win(cta[:, :, z], 600, 200), "check_cta_a_axial_z-12.png")
    save(win(mip(cta, 2, z - 6, z + 5), 600, 200), "check_cta_b_cow_mip.png")
    y = k(0, 1)
    save(win(mip(cta, 1, y - 7, y + 7), 600, 200), "check_cta_c_coronal_mip.png")
    x = k(0, 0)
    save(win(mip(ctv, 0, x - 5, x + 4), 600, 200), "check_cta_d_ctv_sagittal_mip.png")
    z2 = k(-20, 2)
    save(win(mip(ctv, 2, z2 - 6, z2 + 6), 600, 200), "check_cta_e_ctv_axial_mip.png")
    save(win(cta[:, :, k(15, 2)], 80, 40), "check_cta_f_brain_window.png")


def main():
    G = bv.Grid()
    img = nib.load(str(DATA / "ct.nii.gz"))
    ct = np.asarray(img.dataobj).astype(np.float32)
    anat = np.asarray(nib.load(str(DATA / "labels_anatomy.nii.gz")).dataobj).astype(np.uint8)
    dura = np.asarray(nib.load(str(DATA / "labels_dura.nii.gz")).dataobj).astype(np.uint8)
    sigma, n = measure_noise(ct, anat)
    print(f"CT noise sigma (median local SD over {n} WM voxels): {sigma:.2f} HU")
    items = vessel_items(G, dura)
    res = {}
    for name, ph in PHASES.items():
        F, A = phase_fields(G, items, ph)
        vol, Fb = simulate(ct, F, A, sigma, np.random.default_rng(7 if name == "cta" else 8))
        o = nib.Nifti1Image(vol, img.affine, img.header)
        o.set_data_dtype(np.int16)
        nib.save(o, str(DATA / f"{name}.nii.gz"))
        res[name] = vol
        print(f"{name}: wrote {DATA / (name + '.nii.gz')}  enhanced voxels={(Fb > 0.01).sum()}")
    render_checks(G, res["cta"], res["ctv"])
    return res


if __name__ == "__main__":
    main()

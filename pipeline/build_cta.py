"""Simulated contrast-enhanced head CT: CTA (arterial phase) and CTV (venous phase).

Inputs : public/data/ct.nii.gz (real non-contrast CT), vessel centrelines + true radii from
         build_vessels.prepare_geometry (vessels_mni.yaml + sinuses derived from labels_dura).
Outputs: public/data/cta.nii.gz, public/data/ctv.nii.gz (int16 HU, same grid/affine as ct.nii.gz),
         cache/check_cta2_*.png.
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
PHASES = {   # arterial phase: bright arteries, barely opacified veins; venous phase: the reverse
    "cta": dict(big=(380, 380), taper=(380, 330), small=(350, 350), sinus=80, deep=60, cav=120),
    "ctv": dict(big=(100, 100), taper=(100, 100), small=(100, 100), sinus=300, deep=300, cav=260),
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


def vessel_items(G, dura, ct):
    """List of (vid, centreline paths in world mm or None for the volumetric cavernous sinus, radius, def, side sign).
    Left-side paths of CT-snapped vessels (ICA, vertebral) are the independently snapped ones."""
    vessels, vd, _ = bv.prepare_geometry(G, dura, bv.load_vessel_defs(), ct)
    out = []
    for d, _ in vessels:
        vid = d["id"]; r = float(d.get("radius", 0))
        for sgn in ([1, -1] if d.get("mirror") else [1]):
            if bv.is_volumetric(d):
                out.append((vid, None, r, d, sgn))
            else:
                out.append((vid, bv.side_paths(vd, vid, sgn), r, d, sgn))
    return out


def _merge(F0, A0, f, a):
    """Combine vessel fields where they overlap: fraction = max, HU = fraction-weighted mean."""
    Fn = np.maximum(F0, f)
    tot = F0 + f
    An = np.where(tot > 0, (A0 + a) / np.maximum(tot, 1e-6) * Fn, 0)
    return Fn, An.astype(np.float32)


def phase_fields(G, ct, items, ph):
    """Return (F, A): partial-volume fraction and f*HU field (before PSF blur)."""
    shape = G.shape
    F = np.zeros(shape, np.float32); A = np.zeros(shape, np.float32)
    for vid, paths, r, d, sgn in items:
        if paths is None:                                     # cavernous sinus: soft non-bone ellipsoid
            if "ellipsoid" in d:
                sl, f = bv.ellipsoid_field(G, ct, d["ellipsoid"], sgn)
            else:                                             # legacy rounded box with a 1-voxel soft edge
                box = d["box"]
                xs = np.array(box["x"]) * sgn
                lo = np.array([xs.min(), box["y"][0], box["z"][0]], float)
                hi = np.array([xs.max(), box["y"][1], box["z"][1]], float)
                v0 = nib.affines.apply_affine(G.inv, lo); v1 = nib.affines.apply_affine(G.inv, hi)
                a0 = np.minimum(v0, v1); a1 = np.maximum(v0, v1)
                sl = tuple(slice(max(int(np.floor(a0[k])) - 3, 0), min(int(np.ceil(a1[k])) + 4, shape[k])) for k in range(3))
                ii = np.stack(np.meshgrid(*[np.arange(s.start, s.stop) for s in sl], indexing="ij"), -1)
                w = nib.affines.apply_affine(G.A, ii.reshape(-1, 3)).reshape(ii.shape)
                rad = 3.0
                q = np.maximum(lo - w, w - hi) + rad
                sd = np.linalg.norm(np.maximum(q, 0), axis=-1) + np.minimum(q.max(-1), 0) - rad
                f = np.clip(0.5 - sd, 0, 1).astype(np.float32)
            # volumetric sinus fills only what the vessels already in the field leave free (the ICA keeps
            # its own lumen HU where it runs through the cavernous sinus)
            fr = f * (1 - F[sl])
            F[sl] = F[sl] + fr
            A[sl] = A[sl] + fr * lumen(vid, 0, ph)
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


def render_checks(G, ct, cta, ctv):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    A = G.A
    k = lambda x, a: int(round((x - A[a, 3]) / A[a, a]))      # world mm -> voxel index

    def win(img, W, L):
        return np.clip((img - (L - W / 2)) / W, 0, 1)

    def crop(img, xr, yr):                                     # img[x, y] -> window of world mm ranges
        return img[k(xr[0], 0):k(xr[1], 0), k(yr[0], 1):k(yr[1], 1)]

    def save(imgs, name, titles=None, scale=7, ncols=None):   # list of img[a, b] (a horizontal, b up), in a grid
        n = len(imgs)
        nc = ncols or n
        nr = (n + nc - 1) // nc
        w, h = imgs[0].shape
        fig, axs = plt.subplots(nr, nc, figsize=(scale * nc * w / max(w, h) + 0.1, nr * (scale * h / max(w, h) + (0.4 if titles else 0))), dpi=100)
        axs = np.atleast_1d(axs).ravel()
        for ax in axs[n:]:
            ax.axis("off")
        for i, ax in enumerate(axs[:n]):
            ax.imshow(imgs[i].T, cmap="gray", origin="lower", vmin=0, vmax=1, interpolation="nearest"); ax.axis("off")
            if titles:
                ax.set_title(titles[i], fontsize=9)
        plt.tight_layout(pad=0.1)
        fig.savefig(str(CACHE / name), dpi=100); plt.close(fig)

    def slab(vol, axis, c, half_lo, half_hi):                  # MIP over world-mm slab [c-half_lo, c+half_hi]
        i = k(c, axis)
        return mip(vol, axis, i - half_lo, i + half_hi)

    # (a) axial z=-30 around the sella
    z = k(-30, 2)
    save([crop(win(cta[:, :, z], 600, 200), (-45, 45), (-40, 40)), crop(win(ct[:, :, z], 600, 200), (-45, 45), (-40, 40))], "check_cta2_a_axial_z-30_sella.png", ["CTA z=-30", "CT z=-30"], scale=6)
    # (b) coronal 15 mm MIP at y=-12: soft window, bone window, enhancement-only (cta-ct)
    ya = (-70, 30)
    zz = (k(-72, 2), k(40, 2))
    cor = lambda v: slab(v, 1, -12, 7, 7)[:, zz[0]:zz[1]]
    diff = np.clip(cta - ct, 0, None)
    save([cor(win(cta, 600, 200)), cor(win(cta, 2000, 500)), cor(win(diff, 400, 200))], "check_cta2_b_coronal_mip_y-12.png",
         ["CTA soft W600/L200", "CTA bone W2000/L500", "CTA - CT (enhancement) W400/L200"], scale=6)
    # (c) axial z = -45, -55, -68 (soft + bone window)
    imgs, tt = [], []
    for zv in (-45, -55, -68):
        for (W, L, nm) in ((600, 200, "soft"), (2000, 500, "bone")):
            imgs.append(crop(win(cta[:, :, k(zv, 2)], W, L), (-60, 60), (-65, 25))); tt.append(f"CTA z={zv} {nm}")
    save(imgs, "check_cta2_c_axial_skullbase.png", tt, scale=5.5, ncols=2)
    # (d) circle of Willis, 12 mm axial MIP at z=-12
    save([crop(slab(cta, 2, -12, 6, 5), (-55, 55), (-50, 45)) if False else crop(win(slab(cta, 2, -12, 6, 5), 600, 200), (-55, 55), (-50, 45))],
         "check_cta2_d_cow_mip_z-12.png")
    # (e) CTV midline sagittal 10 mm MIP
    save([win(slab(ctv, 0, 0, 5, 4), 600, 200)[k(-120, 1):k(90, 1), k(-72, 2):k(100, 2)]], "check_cta2_e_ctv_sagittal_mip.png")
    # (f) CTA vs CTV, axial 12 mm slab at z=-20
    save([crop(win(slab(v, 2, -20, 6, 5), 600, 200), (-70, 70), (-115, 75)) for v in (cta, ctv)], "check_cta2_f_cta_vs_ctv_z-20.png",
         ["CTA", "CTV"], scale=6)


def artefact_scan(G, ct, cta, ctv, items):
    """Report axis-aligned blocky changes and any change far from every vessel."""
    from scipy.spatial import cKDTree
    near = np.zeros(G.shape, bool)
    for vid, paths, r, d, sgn in items:
        if paths is None:
            sl, f = bv.ellipsoid_field(G, ct, d["ellipsoid"], sgn) if "ellipsoid" in d else (None, None)
            if sl is not None:
                near[sl] |= f > 0
            continue
        for p in paths:
            v = np.round(nib.affines.apply_affine(G.inv, bv.resample(p, step=0.5))).astype(int)
            v = v[np.all((v >= 0) & (v < G.shape), axis=1)]
            near[v[:, 0], v[:, 1], v[:, 2]] = True
    dist = ndi.distance_transform_edt(~near)
    for name, vol in (("cta", cta), ("ctv", ctv)):
        chg = vol != ct.astype(np.int16)
        print(f"{name}: {chg.sum()} voxels differ from ct; max distance of a changed voxel to a vessel centreline/ellipsoid = "
              f"{dist[chg].max():.1f} mm; changed voxels > 8 mm away: {(chg & (dist > 8)).sum()}")
        big = np.abs(vol.astype(np.float32) - ct) > 30
        lab, n = ndi.label(big)
        objs = ndi.find_objects(lab)
        sizes = ndi.sum(big, lab, range(1, n + 1))
        worst = []
        for i, sl in enumerate(objs):
            dims = np.array([s_.stop - s_.start for s_ in sl])
            fill = sizes[i] / np.prod(dims)
            if sizes[i] >= 150 and dims.min() >= 5 and fill > 0.6:
                worst.append((fill, int(sizes[i]), dims.tolist(), [(int(s_.start + G.A[a, 3]), int(s_.stop + G.A[a, 3])) for a, s_ in enumerate(sl)]))
        worst.sort(reverse=True)
        print(f"  |{name}-ct| > 30: {n} components; blocky candidates (fill>0.6, min dim>=5 mm, >=150 vox): {len(worst)}")
        for w in worst[:6]:
            print("   ", w)


def main():
    G = bv.Grid()
    img = nib.load(str(DATA / "ct.nii.gz"))
    ct = np.asarray(img.dataobj).astype(np.float32)
    anat = np.asarray(nib.load(str(DATA / "labels_anatomy.nii.gz")).dataobj).astype(np.uint8)
    dura = np.asarray(nib.load(str(DATA / "labels_dura.nii.gz")).dataobj).astype(np.uint8)
    sigma, n = measure_noise(ct, anat)
    print(f"CT noise sigma (median local SD over {n} WM voxels): {sigma:.2f} HU")
    items = vessel_items(G, dura, ct)
    res = {}
    for name, ph in PHASES.items():
        F, A = phase_fields(G, ct, items, ph)
        vol, Fb = simulate(ct, F, A, sigma, np.random.default_rng(7 if name == "cta" else 8))
        o = nib.Nifti1Image(vol, img.affine, img.header)
        o.set_data_dtype(np.int16)
        nib.save(o, str(DATA / f"{name}.nii.gz"))
        res[name] = vol
        print(f"{name}: wrote {DATA / (name + '.nii.gz')}  enhanced voxels={(Fb > 0.01).sum()}")
    artefact_scan(G, ct, res["cta"], res["ctv"], items)
    render_checks(G, ct, res["cta"], res["ctv"])
    return res


if __name__ == "__main__":
    main()

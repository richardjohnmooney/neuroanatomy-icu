"""Herniation landmark layer (`landmarks`): uncus, cerebral peduncles, oculomotor nerves,
cerebellar tonsils, foramen magnum.  Writes public/data/labels_landmarks.nii.gz + meshes.

Values: 1 uncus, 2 cerebral peduncle, 3 oculomotor nerve, 4 cerebellar tonsil, 5 foramen magnum.
All coordinates MNI152 mm RAS.  Reads labels_anatomy, labels_dura, ct (all already built).
"""
import numpy as np
import nibabel as nib
from scipy import ndimage as ndi
from common import CACHE, DATA, MESH_DIR, save_nifti, structures
import build_vessels as bv
import build_meshes as bm

FM_Z = -65.0           # foramen magnum level (horizontal ring centre), set from the CT in foramen_magnum()
FM_HALF = 1.5          # half thickness in z

# CN III centreline (right side, +x) relative to vessels_mni.yaml: PCA P1 runs (0,-13,-13)->(9,-13,-12),
# PCom (13,3,-14)->(9,-13,-12), cavernous sinus ellipsoid centre (14,3,-31) radii (5.5,10,5.5).
# The nerve leaves the interpeduncular fossa ~4-5 mm below P1, passes lateral to the PCom and
# below it, to the posterior-superior edge of the cavernous sinus.
CN3 = [[3.5, -14, -17.5], [6.5, -12.5, -18.5], [10, -9, -20], [12.5, -5, -23], [14, -2, -26.5]]
CN3_R = 1.2


def world_grid(shape, A):
    ax = [A[i, i] * np.arange(shape[i]) + A[i, 3] for i in range(3)]
    return np.meshgrid(*ax, indexing="ij")


def raster_tube(shape, A, pts, r):
    """Voxels within r mm of the polyline (>=1 voxel thick)."""
    P = bv.resample(np.asarray(pts, float), step=0.3)
    inv = np.linalg.inv(A)
    out = np.zeros(shape, bool)
    vox = np.rint(P @ inv[:3, :3].T + inv[:3, 3]).astype(int)
    for v in vox:
        out[tuple(v)] = True
    # distance transform from centreline voxels, in a cropped box
    lo = np.maximum(vox.min(0) - 4, 0); hi = np.minimum(vox.max(0) + 5, shape)
    sl = tuple(slice(a, b) for a, b in zip(lo, hi))
    d = ndi.distance_transform_edt(~out[sl], sampling=np.abs(np.diag(A)[:3]))
    out[sl] |= d <= r
    return out


def foramen_magnum(ct, A, shape):
    global FM_Z
    X, Y, Z = world_grid(shape, A)
    best = None
    for z in range(-70, -55):
        k = int(round((z - A[2, 3]) / A[2, 2]))
        bone = ndi.gaussian_filter(ct[:, :, k].astype(np.float32), 0.6) > 200
        soft = ~ndi.binary_closing(bone, iterations=1)
        soft &= (np.hypot(X[:, :, k], Y[:, :, k] + 48) < 28)
        lab, n = ndi.label(soft)
        c = lab[int(round(0 - A[0, 3])), int(round(-48 - A[1, 3]))]
        if c == 0:
            continue
        reg = lab == c
        area = reg.sum()
        touches = (reg & (np.hypot(X[:, :, k], Y[:, :, k] + 48) > 26)).any()
        print(f"  FM scan z={z}: enclosed region area={area} mm2 leaks={touches}")
        if not touches and 200 < area < 1500:
            if best is None or area < best[1]:
                best = (z, area, reg)
    return best


def main():
    anat_i = nib.load(str(DATA / "labels_anatomy.nii.gz"))
    A = anat_i.affine
    anat = np.asarray(anat_i.dataobj)
    ct = np.asarray(nib.load(str(DATA / "ct.nii.gz")).dataobj).astype(np.float32)
    shape = anat.shape
    X, Y, Z = world_grid(shape, A)
    lab = np.zeros(shape, np.uint8)
    side = {"R": X >= 0, "L": X < 0}

    # --- foramen magnum
    global FM_Z
    best = foramen_magnum(ct, A, shape)
    if best is None:
        print("  FM: no complete ring found, using default z", FM_Z)
        zc = FM_Z
        raise SystemExit("no ring")
    zc, area, reg = best
    FM_Z = float(zc)
    print(f"foramen magnum level z={zc} (area {area} mm2)")
    inner = ndi.binary_erosion(reg, structure=np.ones((3, 3)), iterations=3)
    ring2d = reg & ~inner
    k0 = int(round((zc - FM_HALF - A[2, 3]))); k1 = int(round((zc + FM_HALF - A[2, 3])))
    fm = np.zeros(shape, bool)
    for k in range(k0, k1 + 1):
        fm[:, :, k] = ring2d
    fm_ring = fm

    # --- tonsils: lowest ~12 mm of cerebellum, medial, y -60..-40
    cb = anat == 10
    cbz = np.argwhere(cb)[:, 2].min() + A[2, 3]
    print(f"cerebellum bottom z={cbz}")
    tmask = cb & (np.abs(X) >= 2) & (np.abs(X) <= 14) & (Y >= -62) & (Y <= -46) & (Z <= FM_Z + 10) & (Z >= FM_Z + 2)
    tmask = ndi.binary_opening(tmask, iterations=1)

    # --- uncus
    cand = np.isin(anat, [3, 6, 24]) & (Y >= -15) & (Y <= 2) & (Z >= -32) & (Z <= -14) & (np.abs(X) >= 8)
    unc = np.zeros(shape, bool)
    for s, sg in (("R", 1), ("L", -1)):
        c = cand & side[s]
        ax = np.abs(X)
        # per (y,z) row: medial-most x of candidate, keep the medial UNC_DEPTH mm
        big = np.where(c, ax, 999)
        med = big.min(0, keepdims=True)
        u = c & (ax <= med + UNC_DEPTH)
        unc |= u
    unc = ndi.binary_opening(unc, iterations=1)
    # keep largest component per side
    for s in ("R", "L"):
        m = unc & side[s]
        l, n = ndi.label(m)
        if n > 1:
            sz = ndi.sum(m, l, range(1, n + 1)); unc[m & (l != 1 + np.argmax(sz))] = False

    # --- peduncles
    mid = anat == 11
    ped = np.zeros(shape, bool)
    for s in ("R", "L"):
        m = mid & side[s]
        ymid = (Y[m].min() + Y[m].max()) / 2
        ped |= m & (Y >= ymid) & (np.abs(X) >= 3)

    # --- oculomotor
    cn = np.zeros(shape, bool)
    for sg in (1, -1):
        cn |= raster_tube(shape, A, [[sg * p[0], p[1], p[2]] for p in CN3], CN3_R)

    lab[unc] = 1
    lab[ped] = 2
    lab[tmask] = 4
    lab[fm_ring] = 5
    lab[cn] = 3         # nerve last so it is never hidden
    save_nifti(lab, DATA / "labels_landmarks.nii.gz", np.uint8)

    # nerve meshes: smooth tubes
    for tag, sg in (("R", 1), ("L", -1)):
        m = bv.tube_mesh(np.array([[sg * p[0], p[1], p[2]] for p in CN3]), CN3_R)
        m.export(MESH_DIR / f"oculomotor-nerve_{tag}.glb")

    # other meshes (marching cubes); skip the nerve (self-meshed)
    img_lab = lab
    nib_img = nib.load(str(DATA / "labels_landmarks.nii.gz"))
    import trimesh
    left = np.broadcast_to((X < 0), shape)
    for s in structures():
        if s["layer"] != "landmarks" or s["id"] == "oculomotor-nerve":
            continue
        base = lab == s["labelValue"]
        for fn in s["meshes"]:
            m = base & (left if fn.endswith("_L.glb") else ~left) if s.get("lateralised") else base
            mesh = bm.mask_to_mesh(m, A, sigma=bm.SIGMA.get("landmarks", 0.7))
            mesh.export(MESH_DIR / fn)
    report(lab, A, X, Y, Z)
    check_images(lab, ct, anat)


UNC_DEPTH = 7.5


def report(lab, A, X, Y, Z):
    names = {1: "uncus", 2: "peduncle", 3: "CN III", 4: "tonsil", 5: "foramen magnum"}
    for v, n in names.items():
        for s, mk in (("R", X >= 0), ("L", X < 0)):
            m = (lab == v) & mk
            if m.sum() == 0:
                continue
            if v == 5 and s == "L":
                continue
            mm = (lab == v) if v == 5 else m
            c = (X[mm].mean(), Y[mm].mean(), Z[mm].mean())
            print(f"{n:15s} {'' if v==5 else s}  vox={mm.sum():6d}  mL={mm.sum()/1000:.2f}  centroid=({c[0]:.1f},{c[1]:.1f},{c[2]:.1f})")
    print("values:", np.unique(lab))



def check_images(lab, ct, anat):
    """PNG checks -> cache/check_landmarks_*.png (CT brain window, landmark colours, tentorium + midbrain outlines)."""
    from PIL import Image
    dura = np.asarray(nib.load(str(DATA / "labels_dura.nii.gz")).dataobj)
    cols = {s["labelValue"]: tuple(int(s["color"][i:i + 2], 16) for i in (1, 3, 5))
            for s in structures() if s["layer"] == "landmarks"}
    off = np.array([-91, -126, -72])
    SC = 5

    def panel(plane, c, box):
        # plane 'ax' (c=z), 'cor' (c=y), 'sag' (c=x); box = (h0,h1,v0,v1) world mm of the two in-plane axes
        i = int(c - off[{"ax": 2, "cor": 1, "sag": 0}[plane]])
        sl = {"ax": lambda a: a[:, :, i], "cor": lambda a: a[:, i, :], "sag": lambda a: a[i, :, :]}[plane]
        ax_off = {"ax": (off[0], off[1]), "cor": (off[0], off[2]), "sag": (off[1], off[2])}[plane]
        h0, h1, v0, v1 = box
        c2 = sl(ct); l2 = sl(lab); a2 = sl(anat); d2 = sl(dura)
        sub = (slice(int(h0 - ax_off[0]), int(h1 - ax_off[0])), slice(int(v0 - ax_off[1]), int(v1 - ax_off[1])))
        def prep(a):
            return a[sub].T[::-1]
        g = np.clip((prep(c2) - 0) / 80 * 255, 0, 255).astype(np.uint8)
        rgb = np.stack([g] * 3, -1).astype(np.float32)
        L = prep(l2)
        for v, col in cols.items():
            m = L == v
            rgb[m] = (np.array(col) if v == 5 else 0.35 * rgb[m] + 0.65 * np.array(col))
        def outline(m, col):
            e = m & ~ndi.binary_erosion(m)
            rgb[e] = col
        outline(prep(a2) == 11, (0, 200, 255))
        outline(prep(d2) == 2, (255, 255, 0))
        im = Image.fromarray(rgb.astype(np.uint8)).resize((rgb.shape[1] * SC, rgb.shape[0] * SC), Image.NEAREST)
        return im

    def save(name, panels):
        w = sum(p.width for p in panels); h = max(p.height for p in panels)
        out = Image.new("RGB", (w + 4 * len(panels), h))
        x = 0
        for p in panels:
            out.paste(p, (x, 0)); x += p.width + 4
        out.save(CACHE / f"check_landmarks_{name}.png")

    save("axial_uncus", [panel("ax", z, (-40, 40, -60, 20)) for z in (-12, -16, -20, -26)])
    save("axial_low", [panel("ax", z, (-40, 40, -75, 5)) for z in (-58, -62, -65, -67)])
    save("coronal", [panel("cor", y, (-45, 45, -45, 5)) for y in (-4, -10, -16, -22)])
    save("sagittal", [panel("sag", x, (-85, 15, -72, 5)) for x in (6, 0, -6, 14)])
    save("sagittal_lat", [panel("sag", x, (-85, 15, -72, 5)) for x in (-10, -18, 10, 18)])


if __name__ == "__main__":
    main()

"""Build labels_vessels.nii.gz + smooth tube meshes for the vessel layer.

Inputs : vessels_mni.yaml (hand-authored centrelines, MNI world mm RAS), labels_dura.nii.gz,
         labels_anatomy.nii.gz, ct.nii.gz.
Outputs: public/data/labels_vessels.nii.gz (uint8, same grid as labels_anatomy; tubes with radius
         max(r, 1.5 mm)), public/data/meshes/<vessel>[_L|_R].glb (true radius), cache/check_vessels_*.png.

Venous sinuses are derived from the dura labels where the YAML says `derive:` (falling back to the
YAML path if derivation fails):
  superior sagittal  = upper rim of the falx (sagittal projection) pulled 2.5 mm inside, front to confluence
  inferior sagittal  = lowest falx voxel per y-slice (anterior part), 1.5 mm above the free edge
  straight sinus     = top of the tentorium ridge at the midline, vein of Galen -> confluence
  transverse sinus   = outer posterolateral rim of the tentorium, confluence -> y -62 (sigmoid snapped on)
Mirroring: `mirror: true` entries are authored for the RIGHT (+x); left = x -> -x.

CT snapping (`snap:` in the YAML): the leading extracranial/petrous points of the ICA (`carotid_canal`,
z < -40) and the vertebral V3 loop (`c1_vertebral`, z < -64 or (y < -30 and |x| > 12) or z <= -52) are replaced by a
minimum-cost path through ct.nii.gz (soft tissue cheap, bone/air dear, bone-clearance bonus so the path
centres in channels, quadratic penalty for leaving a corridor around the YAML guess) between a free start
at the bottom of the grid and the first un-snapped YAML point. Each side is snapped independently on the CT
(the left uses the mirrored YAML guesses). See `snap_path`.

Cavernous sinus = soft ellipsoid (`ellipsoid: {centre, radii}`, or legacy `box:`) restricted to non-bone
voxels (CT < ~150 HU, no air) with a soft edge; see `ellipsoid_field`.
"""
import colorsys
import numpy as np
import nibabel as nib
import trimesh
import yaml
from scipy import ndimage as ndi
from scipy.interpolate import splprep, splev
from scipy.spatial import cKDTree
from skimage.graph import MCP_Geometric
from skimage.morphology import ball
from skimage import measure
from common import CACHE, DATA, MESH_DIR, grid_affine, save_nifti, structures

MIN_RASTER_R = 1.5
SIDES = 14            # tube cross-section segments
STEP = 0.4            # mm resampling along the tube
PARENCHYMA = [1, 2, 3, 4, 5, 6, 20, 21, 22, 23, 24, 11, 12, 13]
CSF = [31, 32, 33, 40, 41, 42, 43, 44, 45]


# ----------------------------------------------------------------------------- geometry helpers
def arclen(p):
    return np.r_[0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]


def resample(p, step=STEP, smooth=0.0):
    """Cubic-spline resample a polyline (through the points) at ~step mm spacing."""
    p = np.asarray(p, float)
    keep = np.r_[True, np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-6]
    p = p[keep]
    if len(p) < 2:
        return p
    u = arclen(p)
    k = min(3, len(p) - 1)
    tck, _ = splprep(p.T, u=u, k=k, s=smooth)
    n = max(int(np.ceil(u[-1] / step)) + 1, 2)
    return np.array(splev(np.linspace(0, u[-1], n), tck)).T


def smooth_line(p, sigma_mm=3.0, step=1.0):
    """Uniform arclength resample then Gaussian smooth; end points kept."""
    p = np.asarray(p, float)
    u = arclen(p)
    n = max(int(u[-1] / step) + 1, 3)
    uu = np.linspace(0, u[-1], n)
    q = np.stack([np.interp(uu, u, p[:, i]) for i in range(3)], 1)
    s = ndi.gaussian_filter1d(q, sigma_mm / step, axis=0, mode="nearest")
    s[0], s[-1] = p[0], p[-1]
    return s


def tube_mesh(path, r, sides=SIDES):
    """Swept circle with parallel-transport frames, flat caps + sphere end joints. Outward normals."""
    P = resample(path)
    T = np.gradient(P, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True) + 1e-12
    a = np.array([0, 0, 1.0]) if abs(T[0, 2]) < 0.9 else np.array([1.0, 0, 0])
    N = np.cross(T[0], a); N /= np.linalg.norm(N)
    frames = []
    for i in range(len(P)):
        if i:
            v = np.cross(T[i - 1], T[i]); s = np.linalg.norm(v)
            if s > 1e-9:
                ang = np.arctan2(s, np.dot(T[i - 1], T[i]))
                ax = v / s
                N = N * np.cos(ang) + np.cross(ax, N) * np.sin(ang) + ax * np.dot(ax, N) * (1 - np.cos(ang))
            N = N - T[i] * np.dot(N, T[i]); N /= np.linalg.norm(N)
        frames.append((N.copy(), np.cross(T[i], N)))
    th = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    V = np.concatenate([P[i] + r * (np.cos(th)[:, None] * frames[i][0] + np.sin(th)[:, None] * frames[i][1])
                        for i in range(len(P))])
    F = []
    for i in range(len(P) - 1):
        for j in range(sides):
            a0, a1 = i * sides + j, i * sides + (j + 1) % sides
            b0, b1 = a0 + sides, a1 + sides
            F += [[a0, a1, b1], [a0, b1, b0]]
    V = np.vstack([V, P[0], P[-1]])
    c0, c1 = len(V) - 2, len(V) - 1
    for j in range(sides):
        F.append([c0, (j + 1) % sides, j])
        e = (len(P) - 1) * sides
        F.append([c1, e + j, e + (j + 1) % sides])
    m = trimesh.Trimesh(V, np.array(F), process=False)
    if m.volume < 0:
        m.invert()
    parts = [m]
    for q in (P[0], P[-1]):
        sph = trimesh.creation.icosphere(subdivisions=2, radius=r * 1.0)
        sph.apply_translation(q)
        parts.append(sph)
    return trimesh.util.concatenate(parts)


def mirror_mesh(m):
    m = m.copy()
    m.vertices = m.vertices * np.array([-1, 1, 1])
    m.invert()
    return m


def rounded_box_mesh(box, rad=3.0, h=0.5):
    x0, x1 = box["x"]; y0, y1 = box["y"]; z0, z1 = box["z"]
    c = np.array([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2])
    half = np.array([x1 - x0, y1 - y0, z1 - z0]) / 2
    pad = 3
    gx = np.arange(x0 - pad, x1 + pad + h, h); gy = np.arange(y0 - pad, y1 + pad + h, h)
    gz = np.arange(z0 - pad, z1 + pad + h, h)
    G = np.stack(np.meshgrid(gx, gy, gz, indexing="ij"), -1)
    q = np.abs(G - c) - (half - rad)
    sdf = np.linalg.norm(np.maximum(q, 0), axis=-1) + np.minimum(q.max(-1), 0) - rad
    v, f, _, _ = measure.marching_cubes(sdf, 0.0, spacing=(h, h, h))
    v = v + np.array([gx[0], gy[0], gz[0]])
    m = trimesh.Trimesh(v, f, process=True)
    m.fix_normals()
    if m.volume < 0:
        m.invert()
    return m


# ----------------------------------------------------------------------------- CT snapping
_SM = {}      # one-entry caches keyed by id(ct)


def smooth_ct(ct, sigma=0.7):
    if _SM.get("hs_id") != id(ct):
        _SM["hs"], _SM["hs_id"] = ndi.gaussian_filter(ct, sigma), id(ct)
    return _SM["hs"]


def snap_cost(ct):
    """Per-voxel traversal cost for the snapping path search (>= 1)."""
    if "cost" in _SM and _SM.get("cost_id") == id(ct):
        return _SM["cost"]
    hs = smooth_ct(ct)
    edt = ndi.distance_transform_edt(hs < 300)                     # distance to bone
    c = (1.0 + 25.0 * np.clip((hs - 150) / 250, 0, 1)               # bone
         + 10.0 * np.clip((-100 - hs) / 200, 0, 1)                  # air
         + 2.0 * np.clip(1 - edt / 3.0, 0, 1)                      # prefer channel centres / bone clearance
         + 4.0 * np.clip((35 - hs) / 80, 0, 1)).astype(np.float32)    # prefer soft tissue (20-80 HU) over fat
    _SM["cost"], _SM["cost_id"] = c, id(ct)
    return c


def densify(p, step=0.5):
    p = np.asarray(p, float)
    u = arclen(p)
    n = max(int(np.ceil(u[-1] / step)) + 1, 2)
    uu = np.linspace(0, u[-1], n)
    return np.stack([np.interp(uu, u, p[:, i]) for i in range(3)], 1)


def snap_path(G, ct, pts, n_snap, start_free=4.0, end_free=3.0, corridor=12.0, dev_w=0.02, sigma=2.0, canal_bonus=1.0):
    """Replace pts[:n_snap] by a CT-following path ending at pts[n_snap] (the first fixed point, nudged <= end_free mm out of bone/air).

    Minimum-cost route (skimage MCP_Geometric) inside `corridor` mm of the guessed polyline; the start is
    free within `start_free` mm (in the plane of the first guess) so it can find the channel at the grid
    bottom. The route is Gaussian-smoothed (sigma mm, ends kept). Returns the full new point list."""
    pts = np.asarray(pts, float)
    guide = densify(pts[: n_snap + 1], 0.5)
    gv = nib.affines.apply_affine(G.inv, guide)
    pad = int(corridor) + 2
    lo = np.maximum(np.floor(gv.min(0)).astype(int) - pad, 0)
    hi = np.minimum(np.ceil(gv.max(0)).astype(int) + pad + 1, G.shape)
    sub = tuple(slice(lo[k], hi[k]) for k in range(3))
    ii = np.stack(np.meshgrid(*[np.arange(lo[k], hi[k]) for k in range(3)], indexing="ij"), -1)
    d, _ = cKDTree(gv).query(ii.reshape(-1, 3))
    d = d.reshape(ii.shape[:3])
    cost = snap_cost(ct)[sub]
    if canal_bonus < 1:                                       # soft pockets enclosed by bone (canals/foramina) are cheap
        hs = smooth_ct(ct)[sub]
        enc = ndi.binary_closing(np.pad(hs > 400, 4, mode="edge"), ball(3))[4:-4, 4:-4, 4:-4] & (hs < 300) & (hs > -100)
        cost = np.where(enc, cost * canal_bonus, cost)
    cost = cost + dev_w * d * d
    cost[d > corridor] = 1e4
    v0 = gv[0]
    start = np.argwhere((np.abs(ii[..., 2] - round(v0[2])) < 0.5) & (np.linalg.norm(ii - v0, axis=-1) <= start_free))
    # the first fixed point may itself sit in bone/air: move it to the cheapest voxel within `end_free` mm
    near = np.argwhere(np.linalg.norm(ii - gv[-1], axis=-1) <= end_free)
    cc = cost[tuple(near.T)] + 0.5 * np.linalg.norm(near + lo - gv[-1], axis=1)
    end = tuple(near[np.argmin(cc)])
    anchor = nib.affines.apply_affine(G.A, np.array(end) + lo)
    mcp = MCP_Geometric(cost, fully_connected=True)
    mcp.find_costs([tuple(s_) for s_ in start], [end])
    path = np.array(mcp.traceback(end)) + lo
    P = nib.affines.apply_affine(G.A, path)
    P[-1] = anchor
    P = smooth_line(P, sigma_mm=sigma, step=1.0)
    P[-1] = anchor
    return np.vstack([P, pts[n_snap + 1:]])


def snap_count(kind, pts):
    """Number of leading YAML points that are extracranial/petrous (to be snapped)."""
    ok = []
    for x, y, z in pts:
        if kind == "carotid_canal":
            ok.append(z < -40)
        else:                                                        # c1_vertebral
            ok.append(z < -64 or (y < -30 and abs(x) > 12) or z <= -52)   # V3 loop + foramen-magnum approach
    n = 0
    while n < len(ok) and ok[n]:
        n += 1
    return n


def ellipsoid_field(G, ct, ell, sgn, soft=1.5):
    """Soft ellipsoid occupancy (0..1) restricted to non-bone, non-air voxels.
    Returns (slice, field) on the grid sub-block. Edge width `soft` mm plus PSF-like smoothing."""
    c = np.array(ell["centre"], float) * np.array([sgn, 1, 1])
    rad = np.array(ell["radii"], float)
    pad = 5
    a = nib.affines.apply_affine(G.inv, c - rad - pad); b = nib.affines.apply_affine(G.inv, c + rad + pad)
    lo = np.maximum(np.floor(np.minimum(a, b)).astype(int), 0)
    hi = np.minimum(np.ceil(np.maximum(a, b)).astype(int) + 1, G.shape)
    sl = tuple(slice(lo[k], hi[k]) for k in range(3))
    ii = np.stack(np.meshgrid(*[np.arange(lo[k], hi[k]) for k in range(3)], indexing="ij"), -1)
    p = nib.affines.apply_affine(G.A, ii.reshape(-1, 3)).reshape(ii.shape) - c
    k0 = np.linalg.norm(p / rad, axis=-1)
    k1 = np.linalg.norm(p / rad ** 2, axis=-1)
    sd = k0 * (k0 - 1) / np.maximum(k1, 1e-6)                        # first-order signed distance, mm
    f = np.clip(0.5 - sd / soft, 0, 1)
    hs = smooth_ct(ct)[sl]
    nb = np.clip((180 - hs) / 60.0, 0, 1) * np.clip((hs + 200) / 150.0, 0, 1)   # CT<~150 HU, not air
    f = ndi.gaussian_filter((f * nb).astype(np.float32), 0.6)
    return sl, f


def ellipsoid_mesh(G, sl, f):
    pf = np.pad(ndi.gaussian_filter(f, 0.8), 2)
    v, fc, _, _ = measure.marching_cubes(pf, 0.45)
    v = nib.affines.apply_affine(G.A, v - 2 + np.array([s.start for s in sl]))
    m = trimesh.Trimesh(v, fc, process=True)
    m.fix_normals()
    if m.volume < 0:
        m.invert()
    return m


# ----------------------------------------------------------------------------- raster
def raster_path(vol, path, r, value, inv, shape):
    """Paint voxels within r mm of the path polyline where vol == 0 (first writer wins)."""
    P = resample(path, step=0.5)
    vox = nib.affines.apply_affine(inv, P)
    R = int(np.ceil(r)) + 1
    off = np.stack(np.meshgrid(*[np.arange(-R, R + 1)] * 3, indexing="ij"), -1).reshape(-1, 3)
    cand = np.round(vox[:, None, :]).astype(int) + off[None]
    cand = cand.reshape(-1, 3)
    # exact distance from the voxel centre to the sample point
    d = np.linalg.norm(cand - np.repeat(vox, len(off), axis=0), axis=1)
    cand = cand[d <= r]
    ok = np.all((cand >= 0) & (cand < shape), axis=1)
    cand = np.unique(cand[ok], axis=0)
    ii = (cand[:, 0], cand[:, 1], cand[:, 2])
    free = vol[ii] == 0
    vol[tuple(c[free] for c in cand.T)] = value


# ----------------------------------------------------------------------------- derivations from dura
class Grid:
    def __init__(self):
        self.A = grid_affine()
        self.inv = np.linalg.inv(self.A)
        self.o = self.A[:3, 3]
        self.shape = (182, 218, 182)

    def vox2w(self, v):
        return nib.affines.apply_affine(self.A, v)


def derive_sinuses(dura, G):
    """Return dict name -> dict(points ...) of derived centrelines (world mm), plus notes."""
    out, notes = {}, {}
    ox, oy, oz = G.o
    xi0 = int(round(-ox))                     # voxel index of x = 0
    falx = dura == 1
    tent = dura == 2
    # ---------------- superior sagittal sinus: upper/posterior rim of falx, 2.5 mm inside
    F2 = falx.any(0)                          # (y, z) sagittal projection
    dist = ndi.distance_transform_edt(F2)
    M2 = dist >= 2.5
    ys, zs = np.nonzero(M2)
    cy, cz = ys.mean(), zs.mean() + 8         # ray origin (voxel units) inside the falx
    ang = np.deg2rad(np.arange(-35, 245, 0.5))
    pts = []
    for a in ang:
        rr = np.arange(0, 200, 0.25)
        yy = np.round(cy + rr * np.cos(a)).astype(int); zz = np.round(cz + rr * np.sin(a)).astype(int)
        ok = (yy >= 0) & (yy < M2.shape[0]) & (zz >= 0) & (zz < M2.shape[1])
        hit = np.zeros(len(rr), bool); hit[ok] = M2[yy[ok], zz[ok]]
        if hit.any():
            k = np.nonzero(hit)[0][-1]
            pts.append((yy[k], zz[k], a))
    pts = np.array(pts)
    # world coordinates; keep only the part that runs front -> over the top -> back down to the confluence
    W = np.stack([np.zeros(len(pts)), pts[:, 0] + oy, pts[:, 1] + oz], 1)
    # confluence height: bottom of the posterior falx (lowest falx z for y < -88) + 3 mm
    post = falx[:, : int(-88 - oy), :]
    zlow = (np.nonzero(post.any((0, 1)))[0].min() + oz) if post.any() else None
    if zlow is None:
        raise RuntimeError("no posterior falx")
    zc = zlow + 3.0
    # cut after the first point past the apex that is posterior to y=-85 and lower than zc
    apex = np.argmax(W[:, 2])
    cut = len(W)
    for i in range(apex, len(W)):
        if W[i, 2] <= zc and W[i, 1] < -85:
            cut = i + 1
            break
    W = W[:cut]
    # drop duplicate points then smooth
    W = W[np.r_[True, np.any(np.diff(W, axis=0) != 0, axis=1)]]
    sss = smooth_line(W, sigma_mm=4.0)
    out["superior-sagittal-sinus"] = [sss]
    notes["superior-sagittal-sinus"] = "derived"
    conf = sss[-1].copy()
    # ---------------- straight sinus: top of tentorium ridge at the midline
    sl = slice(xi0 - 3, xi0 + 4)
    ridge = []
    for y in np.arange(-47, conf[1] + 4, -1.0):
        j = int(round(y - oy))
        col = tent[sl, j, :].any(0)
        if col.any():
            ridge.append((y, np.nonzero(col)[0].max() + oz))
    if len(ridge) < 10:
        raise RuntimeError("tentorium midline ridge not found")
    ridge = np.array(ridge)
    ridge[:, 1] += 1.5
    # blend: vein of Galen (YAML end) -> ridge -> confluence
    pts3 = np.array([[0, y, z] for y, z in ridge])
    pts3 = np.vstack([[0, -45, 10], pts3[pts3[:, 1] < -48], conf])
    out["straight-sinus"] = [smooth_line(pts3, sigma_mm=3.0)]
    notes["straight-sinus"] = "derived"
    # ---------------- inferior sagittal sinus: free edge of the falx
    edge = []
    for y in np.arange(30, -36, -1.0):
        j = int(round(y - oy))
        col = falx[:, j, :].any(0)
        if col.any():
            edge.append((y, np.nonzero(col)[0].min() + oz + 1.5))
    edge = np.array(edge)
    if len(edge) < 20:
        raise RuntimeError("falx free edge not found")
    iss = np.vstack([[[0, y, z] for y, z in edge], [[0, -42, 16], [0, -45, 10]]])
    out["inferior-sagittal-sinus"] = [smooth_line(iss, sigma_mm=3.0)]
    notes["inferior-sagittal-sinus"] = "derived"
    # ---------------- transverse sinus: outer posterolateral rim of the tentorium (right side, +x)
    rim = []
    for y in np.arange(conf[1] + 2, -61.9, 1.0):
        j = int(round(y - oy))
        sl2 = tent[xi0 + 3:, j, :]
        if not sl2.any():
            continue
        xs_ = np.nonzero(sl2.any(1))[0]
        xm = xs_.max()
        zsel = np.nonzero(sl2[max(xm - 2, 0): xm + 1].any(0))[0]
        rim.append((xm + 3.0, y, zsel.mean() + oz))
    rim = np.array(rim)
    if len(rim) < 15:
        raise RuntimeError("tentorium rim not found")
    rim = np.vstack([conf, rim[rim[:, 0] > 5]])
    ts = smooth_line(rim, sigma_mm=5.0)
    out["transverse-sinus"] = [ts]
    notes["transverse-sinus"] = "derived"
    return out, notes, conf


# ----------------------------------------------------------------------------- main build
def hex2rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)]) / 255.0


def load_vessel_defs():
    spec = yaml.safe_load((__import__("pathlib").Path(__file__).parent / "vessels_mni.yaml").read_text(encoding="utf-8"))
    return spec["arteries"] + spec["veins"]


SNAP_REPORT = {}


def side_paths(vd, vid, sgn):
    """Centreline paths of vessel `vid` for side sgn (+1 right/authored, -1 left): the independently
    CT-snapped left paths if present (`vd[vid + '@L']`), otherwise the mirrored right paths."""
    if sgn < 0 and vid + "@L" in vd:
        return [np.array(p, float) for p in vd[vid + "@L"]]
    return [p * np.array([sgn, 1, 1]) for p in vd[vid]]


def is_volumetric(d):
    return "ellipsoid" in d or "box" in d


def prepare_geometry(G, dura, defs, ct=None):
    """Final per-vessel centrelines (arteries + derived sinuses + snapped sigmoid/confluence).
    Returns (vessels, vd, dnotes): vessels = [(def, paths|None)], vd = id -> paths (RIGHT/authored side,
    CT-snapped where the YAML has `snap:`); vd[id + '@L'] = independently snapped LEFT paths (use side_paths)."""
    if ct is None:
        ct = np.asarray(nib.load(str(DATA / "ct.nii.gz")).dataobj).astype(np.float32)
    sdict = {s["id"]: s for s in structures() if s["layer"] == "vessels"}
    try:
        derived, dnotes, conf = derive_sinuses(dura, G)
    except Exception as e:                                     # fall back wholesale
        print("derivation failed, using YAML sinus paths:", e)
        derived, dnotes, conf = {}, {}, None

    # per-vessel list of (side, [paths]) with the true radius
    vessels = []
    for d in defs:
        vid = d["id"]
        s = sdict[vid]
        if is_volumetric(d):
            vessels.append((d, None))
            continue
        paths = [np.array(p, float) for p in d["paths"]]
        if vid in derived:
            paths = derived[vid]
        vessels.append((d, paths))
    # transverse -> sigmoid continuity and confluence snapping
    vd = {d["id"]: p for d, p in vessels}
    # extracranial / petrous snapping to the CT, each side independently
    for d, _ in vessels:
        kind = d.get("snap")
        if not kind:
            continue
        vid = d["id"]
        base = np.array(d["paths"][0], float)
        for tag, sgn in (("R", 1), ("L", -1)):
            guess = base * np.array([sgn, 1, 1])
            n = snap_count(kind, guess)
            if n == 0:
                continue
            new = snap_path(G, ct, guess, n, canal_bonus=0.35 if kind == "carotid_canal" else 1.0)
            dense = densify(new[: len(new) - (len(guess) - n - 1)], 0.5)
            tree = cKDTree(dense)
            shifts = tree.query(guess[:n])[0]
            SNAP_REPORT[(vid, tag)] = dict(n=n, shifts=shifts, path=dense, guess=guess[:n])
            if sgn > 0:
                vd[vid] = [new] + [np.array(p, float) for p in d["paths"][1:]]
            else:
                vd[vid + "@L"] = [new] + [np.array(p, float) * np.array([-1, 1, 1]) for p in d["paths"][1:]]
    if "transverse-sinus" in derived:
        tend = derived["transverse-sinus"][0][-1].copy()
        sig = vd["sigmoid-sinus"][0].copy()
        delta = tend - sig[0]
        w = 1 - arclen(sig) / arclen(sig)[-1]
        sig = sig + w[:, None] * delta[None]
        vd["sigmoid-sinus"][0] = sig
        dnotes["sigmoid-sinus"] = "YAML path, first point snapped to derived transverse end, offset tapered to 0 at the jugular foramen"
    if conf is not None:
        cf = vd["confluence-of-sinuses"][0]
        vd["confluence-of-sinuses"] = [np.array([conf, conf + np.array([0, -1.0, 0])])]
    # deep veins: end of the straight sinus should coincide with Galen; ok as authored

    return vessels, vd, dnotes


def build():
    G = Grid()
    dura = np.asarray(nib.load(str(DATA / "labels_dura.nii.gz")).dataobj).astype(np.uint8)
    anat = np.asarray(nib.load(str(DATA / "labels_anatomy.nii.gz")).dataobj).astype(np.uint8)
    ct = np.asarray(nib.load(str(DATA / "ct.nii.gz")).dataobj).astype(np.float32)
    sdict = {s["id"]: s for s in structures() if s["layer"] == "vessels"}
    defs = load_vessel_defs()

    vessels, vd, dnotes = prepare_geometry(G, dura, defs, ct)

    lab = np.zeros(G.shape, np.uint8)
    meshes = {}
    centrelines = {}
    for d, _ in vessels:
        vid = d["id"]; s = sdict[vid]; r = float(d.get("radius", 0))
        val = s["labelValue"]
        paths = vd[vid] if not is_volumetric(d) else None
        sides = [("R", 1)] + ([("L", -1)] if d.get("mirror") else [])
        if not d.get("mirror"):
            sides = [("", 1)]
        for tag, sgn in sides:
            if is_volumetric(d):
                if "ellipsoid" in d:
                    sl, f = ellipsoid_field(G, ct, d["ellipsoid"], sgn)
                    mk = np.zeros(G.shape, bool)
                    mk[sl] = (f >= 0.5) & (smooth_ct(ct)[sl] < 150)
                    lab[mk & (lab == 0)] = val
                    meshes[f"{vid}_{tag}.glb"] = ellipsoid_mesh(G, sl, f)
                    continue
                box = d["box"]                                       # legacy rounded box (no CT restriction)
                m = rounded_box_mesh(box)
                if sgn < 0:
                    m = mirror_mesh(m)
                mx = np.array(box["x"]) * sgn
                lo, hi = np.sort(mx)
                ix = np.arange(G.shape[0]) + G.o[0]
                iy = np.arange(G.shape[1]) + G.o[1]
                iz = np.arange(G.shape[2]) + G.o[2]
                mk = ((ix >= lo)[:, None, None] & (ix <= hi)[:, None, None]
                      & ((iy >= box["y"][0]) & (iy <= box["y"][1]))[None, :, None]
                      & ((iz >= box["z"][0]) & (iz <= box["z"][1]))[None, None, :])
                lab[mk & (lab == 0)] = val
                meshes[f"{vid}_{tag}.glb"] = m
                continue
            ps = side_paths(vd, vid, sgn)
            parts = []
            for p in ps:
                raster_path(lab, p, max(r, MIN_RASTER_R), val, G.inv, G.shape)
                parts.append(tube_mesh(p, r))
            m = trimesh.util.concatenate(parts)
            fn = f"{vid}_{tag}.glb" if tag else f"{vid}.glb"
            meshes[fn] = m
            centrelines[(vid, tag)] = ps
    # write
    save_nifti(lab, DATA / "labels_vessels.nii.gz", np.uint8)
    tot = 0
    for fn, m in meshes.items():
        m.export(MESH_DIR / fn)
        sz = (MESH_DIR / fn).stat().st_size
        tot += sz
        print(f"{fn:42s} faces={len(m.faces):6d} {sz/1e3:7.0f} KB centroid=({m.centroid[0]:6.1f},{m.centroid[1]:6.1f},{m.centroid[2]:6.1f})")
    print("total vessel mesh MB: %.2f" % (tot / 1e6))
    for k, v in dnotes.items():
        print(f"sinus {k}: {v}")
    for d, _ in vessels:
        if d.get("derive") and d["id"] not in dnotes:
            print(f"sinus {d['id']}: FALLBACK (YAML path)")
    # check expected files exist
    for s in sdict.values():
        for fn in s["meshes"]:
            if fn not in meshes:
                print("MISSING mesh", fn)
    return G, lab, anat, ct, dura, centrelines, sdict


# ----------------------------------------------------------------------------- checks
def centreline_stats(G, anat, ct, centrelines):
    print("\nartery centreline sanity (median HU, % parenchyma, % CSF label):")
    for (vid, tag), ps in centrelines.items():
        if tag == "L":
            continue
        P = np.vstack([resample(p, step=1.0) for p in ps])
        vox = nib.affines.apply_affine(G.inv, P)
        hu = ndi.map_coordinates(ct, vox.T, order=1, mode="nearest")
        ii = np.clip(np.round(vox).astype(int), 0, np.array(G.shape) - 1)
        al = anat[ii[:, 0], ii[:, 1], ii[:, 2]]
        par = np.isin(al, PARENCHYMA).mean() * 100
        csf = np.isin(al, CSF).mean() * 100
        print(f"  {vid:32s}{tag or '-':2s} medHU={np.median(hu):6.1f}  parenchyma={par:5.1f}%  csf={csf:5.1f}%")


def snap_stats(G, ct):
    """Median HU along each snapped extracranial/petrous path, by segment, and shift from the YAML guesses."""
    print("\nsnapped extracranial paths (CT HU along the centreline; shift = distance YAML guess -> snapped path):")
    for (vid, tag), r in SNAP_REPORT.items():
        P = r["path"]
        hu = ndi.map_coordinates(ct, nib.affines.apply_affine(G.inv, P).T, order=1, mode="nearest")
        z = P[:, 2]
        if vid == "internal-carotid-artery":
            segs = [("cervical z<-60", z < -60), ("vertical canal -60..-48", (z >= -60) & (z < -48)),
                    ("horizontal petrous z>=-48", z >= -48)]
        else:
            u = arclen(P) / arclen(P)[-1]
            segs = [(f"V3 third {i + 1}", (u >= i / 3) & (u <= (i + 1) / 3 + 1e-9)) for i in range(3)]
        txt = "; ".join(f"{n}: {np.median(hu[m]):.0f} HU (max {hu[m].max():.0f})" for n, m in segs if m.any())
        print(f"  {vid:22s}{tag} n_snapped={r['n']} maxshift={r['shifts'].max():.1f} mm "
              f"(per point {np.round(r['shifts'], 1).tolist()}) allMedHU={np.median(hu):.0f} "
              f"frac>300HU={np.mean(hu > 300) * 100:.0f}%\n      {txt}")


def save_png(img, path):
    import matplotlib.pyplot as plt
    plt.imsave(str(path), np.clip(img, 0, 1))


def render_checks(G, lab, ct, sdict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ox, oy, oz = G.o
    LUT = np.zeros((256, 3))
    for s in sdict.values():
        LUT[s["labelValue"]] = hex2rgb(s["color"])
    W = lambda v: np.clip((v - 0) / 80.0, 0, 1)   # W80 L40

    def slab(axis, c, half):
        idx = int(round(c - G.o[axis]))
        sl = [slice(None)] * 3
        sl[axis] = slice(max(idx - half, 0), idx + half + 1)
        return tuple(sl), idx

    def view(axis, c, half, title, fname, xlim, ylim):
        sl, idx = slab(axis, c, half)
        sl1 = [slice(None)] * 3; sl1[axis] = idx
        base = ct[tuple(sl1)]
        sub = lab[sl]
        # first occupied voxel from the viewer side (nearest the high end of the slab)
        sub = np.moveaxis(sub, axis, -1)
        occ = sub > 0
        top = np.where(occ.any(-1), occ.shape[-1] - 1 - np.argmax(occ[..., ::-1], -1), 0)
        vals = np.take_along_axis(sub, top[..., None], -1)[..., 0]
        rgb = np.repeat(W(base)[..., None], 3, -1) * 0.9
        m = vals > 0
        rgb[m] = LUT[vals[m]]
        rgb = np.moveaxis(rgb, 0, 1)         # (second, first, 3)
        a = [i for i in range(3) if i != axis]
        ext = [G.o[a[0]], G.o[a[0]] + G.shape[a[0]], G.o[a[1]], G.o[a[1]] + G.shape[a[1]]]
        fig, ax = plt.subplots(figsize=(9, 9))
        ax.imshow(rgb, origin="lower", extent=ext, interpolation="nearest")
        ax.set_xlim(*xlim); ax.set_ylim(*ylim); ax.set_title(title)
        fig.savefig(str(CACHE / fname), dpi=90); plt.close(fig)

    view(2, -12, 2, "axial z=-12 (slab +-2), CT W80/L40 + vessels", "check_vessels_axial_z-12.png", (-70, 70), (-80, 70))
    view(0, 0, 2, "sagittal x=0 (slab +-2)", "check_vessels_sagittal_x0.png", (-120, 90), (-70, 95))
    view(2, -5, 6, "axial z=-5 posterior (slab +-6)", "check_vessels_axial_z-5_post.png", (-70, 70), (-115, -20))
    view(2, -20, 4, "axial z=-20 posterior (transverse sinuses, slab +-4)", "check_vessels_axial_z-20_post.png", (-75, 75), (-115, -15))
    view(1, 0, 3, "coronal y=0 (slab +-3)", "check_vessels_coronal_y0.png", (-60, 60), (-60, 60))
    # MIP-style projections coloured by structure with depth shading
    arteries_only = np.where(lab < 20, lab, 0)
    for name, axis, xlim, ylim, vol in [("axial", 2, (-70, 70), (-115, 80), lab),
                                        ("sagittal", 0, (-115, 85), (-75, 95), lab),
                                        ("axial_arteries", 2, (-60, 60), (-95, 45), arteries_only),
                                        ("sagittal_arteries", 0, (-95, 50), (-65, 50), arteries_only)]:
        sub = np.moveaxis(vol, axis, -1)
        occ = sub > 0
        top = np.where(occ.any(-1), occ.shape[-1] - 1 - np.argmax(occ[..., ::-1], -1), 0)
        vals = np.take_along_axis(sub, top[..., None], -1)[..., 0]
        m = vals > 0
        shade = 0.55 + 0.45 * (top / occ.shape[-1])
        rgb = np.full(vals.shape + (3,), 0.08)
        rgb[m] = LUT[vals[m]] * shade[m, None]
        a = [i for i in range(3) if i != axis]
        rgb = np.moveaxis(rgb, 0, 1)
        ext = [G.o[a[0]], G.o[a[0]] + G.shape[a[0]], G.o[a[1]], G.o[a[1]] + G.shape[a[1]]]
        fig, ax = plt.subplots(figsize=(9, 9))
        ax.imshow(rgb, origin="lower", extent=ext, interpolation="nearest")
        ax.set_xlim(*xlim); ax.set_ylim(*ylim)
        ax.set_title(f"{name} MIP of vessel labels (colour by structure)")
        fig.savefig(str(CACHE / f"check_vessels_mip_{name}.png"), dpi=90); plt.close(fig)


def main():
    G, lab, anat, ct, dura, centrelines, sdict = build()
    centreline_stats(G, anat, ct, centrelines)
    snap_stats(G, ct)
    render_checks(G, lab, ct, sdict)
    for v, s in sorted(sdict.items(), key=lambda kv: kv[1]["labelValue"]):
        print(f"label {s['labelValue']:2d} {v:32s} voxels={(lab == s['labelValue']).sum()}")


if __name__ == "__main__":
    main()

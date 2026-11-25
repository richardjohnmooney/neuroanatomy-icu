"""Label refinement that needs a real, sharp CT (used by build_labels when ct.nii.gz is the registered real CT).

  brain_mask       intracranial soft tissue + CSF inside the skull inner table (bone > BONE_HU defines the skull)
  tentorium_height height field z = H(x, y) of the tentorium cerebelli: anatomical prior (apex line + lateral slope)
                   refined with the bright sheet visible in the thin-slice CT
  brainstem_model  midbrain / pons / medulla as swept ellipses (landmarks read off the CT), limited by the brain mask
  cerebellum       posterior-fossa brain below the tentorium, behind/lateral to the brainstem
All coordinates are MNI mm (RAS) on the 182x218x182 grid.
"""
import numpy as np
from scipy import ndimage as ndi
from common import GRID_SHAPE, grid_affine

BONE_HU = 300.0
SEED_MM = (0, -15, 15)
BALL = ndi.generate_binary_structure(3, 1)


def axes():
    A = grid_affine()
    return [A[i, i] * np.arange(GRID_SHAPE[i]) + A[i, 3] for i in range(3)]


def grids():
    ax = axes()
    return (ax[0][:, None, None].astype(np.float32), ax[1][None, :, None].astype(np.float32),
            ax[2][None, None, :].astype(np.float32))


def largest(m, structure=None):
    lab, n = ndi.label(m, structure=structure)
    return m if n <= 1 else lab == (1 + int(np.argmax(np.bincount(lab.ravel())[1:])))


# ----------------------------------------------------------------------------------------------- brain mask
def brain_mask(hu, erode=4):
    """Connected soft-tissue (+CSF) component inside the skull. Bone (> BONE_HU, dilated 1 voxel) is the barrier;
    an erosion of `erode` voxels cuts the brain from scalp / orbit / pharynx through skull-base foramina, then it is
    grown back inside the soft-tissue envelope."""
    hs = ndi.gaussian_filter(hu.astype(np.float32), 0.7)
    bone = hu > BONE_HU
    soft = (hs > -8) & (hs < 85) & ~ndi.binary_dilation(bone, iterations=1)
    core = ndi.binary_erosion(soft, BALL, iterations=erode)
    lab, _ = ndi.label(core)
    s = np.round(np.linalg.inv(grid_affine()) @ np.array([*SEED_MM, 1.0]))[:3].astype(int)
    k = lab[tuple(s)]
    if k == 0:
        k = 1 + int(np.argmax(np.bincount(lab.ravel())[1:]))
    m = ndi.binary_dilation(lab == k, BALL, iterations=erode) & soft
    m = ndi.binary_closing(m, BALL, iterations=2)
    m = ndi.binary_fill_holes(m)
    for i in range(m.shape[2]):                    # axial fill too, so nothing stays open at the skull base
        m[:, :, i] = ndi.binary_fill_holes(m[:, :, i])
    m = ndi.binary_opening(m, BALL, iterations=2)  # thin rim against bone / protrusions through foramina
    X, Y, Z = grids()
    m &= ~((np.abs(X) <= 14) & (Y >= -6) & (Y <= 14) & (Z <= -23))      # sella / sphenoid soft tissue below the suprasellar cistern
    m = largest(ndi.binary_opening(m, BALL, iterations=1))
    return ndi.binary_erosion(m, BALL, iterations=1)


# ----------------------------------------------------------------------------------------------- tentorium
TENT_APEX = ([-110, -100, -85, -70, -55, -40, -25], [-24, -20, -14, -6, 0, 0, -5])   # midline z along y
TENT_SLOPE = 0.55                                                                   # mm down per mm lateral


def tentorium_height(hu, bm, window=9.0):
    """H(x, y) (mm) of the tentorium: prior (apex line + slope) + smoothed CT sheet-ridge residual (clipped to +-6)."""
    ax = axes()
    X, Y, Z = np.meshgrid(ax[0], ax[1], ax[2], indexing="ij")
    zp = np.interp(Y, *TENT_APEX) - TENT_SLOPE * np.abs(X)
    c = np.clip(hu, 0, 90).astype(np.float32)
    R = -ndi.gaussian_filter1d(ndi.gaussian_filter(c, [1.0, 1.0, 0.0]), 1.3, axis=2, order=2)   # bright thin sheet
    sc = np.where(bm & (np.abs(Z - zp) <= window), R, -1e9)
    zi = sc.argmax(2)
    ok = (sc.max(2) > 0.5) & (np.abs(X[:, :, 0]) > 4)
    zr = ax[2][zi]
    zp2 = zp[:, :, 0] * 0 + np.take_along_axis(zp, zi[:, :, None], 2)[:, :, 0]
    res = np.where(ok, zr - zp2, 0.0)
    w = ok.astype(np.float32)
    num = ndi.gaussian_filter(res * w, 5.0); den = ndi.gaussian_filter(w, 5.0)
    corr = np.where(den > 0.08, num / np.maximum(den, 1e-6), 0.0) * np.clip(den / 0.3, 0, 1)
    prior2d = np.interp(Y[:, :, 0], *TENT_APEX) - TENT_SLOPE * np.abs(X[:, :, 0])
    return prior2d + np.clip(corr, -6, 6)


# ----------------------------------------------------------------------------------------------- brainstem
# (z, centre y, half-width x, half-length y) of the brainstem cross-section, read off the CT (see check images)
STEM_SECTIONS = [(4, -28, 15, 10), (-10, -26, 18, 13), (-25, -26, 17, 12), (-35, -28, 24, 13),
                 (-45, -29, 21, 12.5), (-55, -33, 13, 9), (-75, -35, 11, 8)]
Z_MID_PONS, Z_PONS_MED = -20.0, -45.0


def brainstem_model(bm):
    """Brainstem (11 midbrain, 12 pons, 13 medulla) = swept ellipse intersected with the brain mask."""
    X, Y, Z = grids()
    zs, cy, rx, ry = (np.array(c, np.float32) for c in zip(*STEM_SECTIONS))
    o = np.argsort(zs); zs, cy, rx, ry = zs[o], cy[o], rx[o], ry[o]
    z1 = Z[0, 0, :]
    cyz, rxz, ryz = (np.interp(z1, zs, v)[None, None, :] for v in (cy, rx, ry))
    ell = (X / rxz) ** 2 + ((Y - cyz) / ryz) ** 2 <= 1.0
    m = ell & bm & (Z <= 4)
    m = largest(ndi.binary_opening(m, BALL, iterations=1))
    out = np.zeros(GRID_SHAPE, np.uint8)
    out[m] = 11
    out[m & (Z <= Z_MID_PONS)] = 12
    out[m & (Z <= Z_PONS_MED)] = 13
    return out


# ----------------------------------------------------------------------------------------------- cerebellum
def cerebellum(bm, H, stem):
    """Posterior-fossa brain below the tentorium surface H(x, y), behind the petrous ridge, not brainstem."""
    X, Y, Z = grids()
    y_ant = -25.0 - 0.5 * np.clip(np.abs(X) - 15.0, 0, None)
    m = bm & (Z < H[:, :, None]) & (Y < y_ant) & (stem == 0)
    m = ndi.binary_opening(m, BALL, iterations=1)
    return largest(m)


# ----------------------------------------------------------------------------------------------- CSF spaces
# On the sharp CT, CSF is genuinely dark (ventricles ~0-12 HU) and parenchyma 30-40 HU; the midpoint is ~20 HU.
T_CSF = 20.0
T_SLIT = 22.0          # thin slits (3rd ventricle) are partial-volumed: use a more generous cut
V3_POLY = [(3, 13), (-6, 14), (-18, 12), (-27, 8), (-31, 2), (-26, -2), (-16, -3), (-12, -6), (-8, -9), (1, -10), (4, -5)]
AQ_PATH = [(-30, -3), (-34, -9), (-38, -14), (-41, -19)]          # (y, z) at x=0; fallback only
AQ_RADIUS = 1.3
MAGENDIE_PATH = [(-44, -44), (-47, -50), (-50, -55)]


def _poly_yz(Y, Z, poly, grow=0.0):
    from matplotlib.path import Path
    yy, zz = np.meshgrid(Y.ravel(), Z.ravel(), indexing="ij")
    m = Path(poly).contains_points(np.stack([yy.ravel(), zz.ravel()], 1)).reshape(yy.shape)
    if grow:
        m = ndi.distance_transform_edt(~m) <= grow
    return m[None, :, :]


def _tube(X, Y, Z, path, r):
    m = np.zeros(np.broadcast(X, Y, Z).shape, bool)
    for (y0, z0), (y1, z1) in zip(path[:-1], path[1:]):
        dy, dz = y1 - y0, z1 - z0
        t = np.clip(((Y - y0) * dy + (Z - z0) * dz) / (dy * dy + dz * dz), 0, 1)
        m |= X ** 2 + (Y - (y0 + t * dy)) ** 2 + (Z - (z0 + t * dz)) ** 2 <= r ** 2
    return m


def csf_labels(out, hu, bm, H, ho_vent):
    """Assign 30-33, 40-45 on the real CT. `out` has brain labels (0 where unlabelled); `ho_vent` = Harvard-Oxford
    ventricle mask (only used as a dilated search region). Returns (out, info)."""
    out = out.copy()
    X, Y, Z = grids()
    ax = np.abs(X)
    hs = ndi.gaussian_filter(hu.astype(np.float32), 0.6)
    near_bone = ndi.maximum_filter(hu, size=3) > 150
    dark = (hs < T_CSF) & (hs > -15) & bm & ~near_bone
    slit = (hs < T_SLIT) & (hs > -15) & bm & ~near_bone
    stem = np.isin(out, (11, 12, 13))
    cb = out == 10
    info = {}

    def put(mask, v, protect=()):
        m = mask & ~np.isin(out, protect) if len(protect) else mask
        out[m] = v
        return m

    # --- lateral ventricles: dark voxels in a dilated Harvard-Oxford region, closed (choroid plexus calcification
    # and partial-volumed septum edges), small components dropped
    reg = ndi.binary_dilation(ho_vent, BALL, iterations=5) & bm
    cand = dark & reg
    cand = ndi.binary_closing(cand, BALL, iterations=2) & reg & ~near_bone
    lab, n = ndi.label(cand, structure=np.ones((3, 3, 3)))
    if n:
        sz = np.bincount(lab.ravel()); sz[0] = 0
        cand = np.isin(lab, np.nonzero(sz >= 150)[0])
    lv = cand
    put(lv, 30)
    info["lateral"] = lv

    # --- third ventricle: the dark midline slit between the thalami inside the sagittal outline
    poly3 = _poly_yz(Y.ravel(), Z.ravel(), V3_POLY)
    r3 = _poly_yz(Y.ravel(), Z.ravel(), V3_POLY, grow=2.0) & (ax <= 3.5) & ~lv
    c3 = slit & r3
    # the slit is 2-4 mm wide and partial-volumed, so it is detected in pieces: bridge them along the slit (z, y)
    c3 = ndi.binary_closing(c3, np.ones((1, 3, 7), bool)) & r3
    c3 = largest(c3 | (ndi.binary_closing(c3, np.ones((1, 5, 9), bool)) & r3 & poly3 & (ax <= 2.2)))
    # geometric fallback only where the CT does not resolve the slit (thalami abut): a 2.4 mm sheet inside the outline
    c3 |= poly3 & (ax <= 1.2) & (hs < 32) & ~lv & bm
    c3 = largest(c3)
    put(c3, 31)
    info["third"] = c3

    # --- fourth ventricle: dark channel behind the pons / upper medulla, in front of the vermis
    r4 = (ax <= 10) & (Y >= -56) & (Y <= -32) & (Z >= -47) & (Z <= -17) & ~stem
    c4 = ndi.binary_opening(dark & r4, BALL, iterations=1)
    c4 = largest(ndi.binary_closing(c4, BALL, iterations=1) & r4 & bm)
    put(c4, 33)
    info["fourth"] = c4

    # --- cisterna magna: dark space behind the medulla / below the vermis and tonsils (z < obex)
    rm = (ax <= 16) & (Y >= -76) & (Y <= -34) & (Z >= -72) & (Z < -46) & ~stem
    cm = ndi.binary_opening(dark & rm, BALL, iterations=1)
    cm = largest(ndi.binary_closing(cm, BALL, iterations=1) & rm & bm)
    put(cm, 44)
    info["magna"] = cm

    # --- quadrigeminal + superior cerebellar cistern: dark cleft between the tentorial leaflets above the vermis
    zt = H[:, :, None]
    rq = (ax <= 12) & (Y >= -90) & (Y <= -28) & (Z >= -20) & (Z <= 10) & (Z <= zt + 3) & (Z >= zt - 14)
    cq = ndi.binary_opening(dark & rq & ~lv & ~c3, BALL, iterations=1)
    cq = largest(cq)
    put(cq, 42)
    info["quadrigeminal"] = cq

    # --- basal cisterns: dark space around the brainstem, classified by position
    d_stem = ndi.distance_transform_edt(~stem)
    # prepontine: between clivus and pons
    rp = (ax <= 14) & (Y >= -26) & (Y <= 0) & (Z >= -46) & (Z <= -14) & (d_stem <= 8)
    cp = largest(ndi.binary_opening(dark & rp & ~stem, BALL, iterations=1))
    put(cp, 43)
    info["prepontine"] = cp
    # interpeduncular: dark fossa in front of the midbrain
    ri = (ax <= 9) & (Y >= -26) & (Y <= -2) & (Z >= -20) & (Z <= 0) & (d_stem <= 6)
    ci = dark & ri & ~stem & (out != 43)
    ci = largest(ndi.binary_opening(ci, BALL, iterations=1)) if ci.any() else ci
    put(ci, 45)
    info["interpeduncular"] = ci
    # suprasellar: above the sella / around the chiasm, in front of the interpeduncular fossa
    rs = (ax <= 20) & (Y >= -4) & (Y <= 24) & (Z >= -22) & (Z <= -2)
    cs = dark & rs & ~np.isin(out, (30, 31, 45, 43))
    cs = ndi.binary_opening(cs, BALL, iterations=1)
    cs = ndi.binary_dilation(largest(cs), BALL, iterations=0) if False else largest(cs)
    put(cs, 40)
    info["suprasellar"] = cs
    # ambient: dark wings round the lateral midbrain, joining the interpeduncular and quadrigeminal cisterns
    ra = (ax >= 2.5) & (ax <= 28) & (Y >= -50) & (Y <= -6) & (Z >= -18) & (Z <= 6) & (d_stem <= 7)
    ca = dark & ra & ~stem & ~np.isin(out, (30, 31, 42, 45, 40, 43))
    ca = ndi.binary_opening(ca, BALL, iterations=1)
    put(ca, 41)
    info["ambient"] = ca
    # --- aqueduct: thin tube joining the 3rd and 4th ventricles (3 mm: not resolved on CT; geometric)
    aq = _tube(X, Y, Z, AQ_PATH, AQ_RADIUS) & bm & np.isin(out, (0, 11, 22, 10, 6, 1, 45, 42))
    put(aq, 32)
    info["aqueduct"] = aq
    return out, info


# ----------------------------------------------------------------------------------------------- midline (falx)
def midline_x(hu, bm, half=6.0):
    """x position (mm) of the dense line in the interhemispheric fissure (falx / fissure vessels) per (y, z),
    from the ridge response across x, robustly smoothed. Returns (xm[y, z], valid[y, z]); midline of the MNI
    template (x = 0) is used where the CT shows no line."""
    ax = axes()
    c = np.clip(hu, -5, 90).astype(np.float32)
    R = -ndi.gaussian_filter1d(ndi.gaussian_filter(c, [0.0, 1.0, 1.0]), 1.2, axis=0, order=2)
    sel = np.abs(ax[0]) <= half
    Rs = np.where(bm[sel], R[sel], -1e9)
    xi = Rs.argmax(0)
    best = Rs.max(0)
    xv = ax[0][sel][xi]
    ok = best > 1.0
    w = ok.astype(np.float32)
    num = ndi.gaussian_filter(np.where(ok, xv, 0.0) * w, 4.0); den = ndi.gaussian_filter(w, 4.0)
    xm = np.where(den > 0.05, num / np.maximum(den, 1e-6), 0.0)
    xm = xm * np.clip(den / 0.25, 0, 1)
    return xm.astype(np.float32), ok

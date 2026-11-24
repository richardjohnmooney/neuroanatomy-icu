"""Build GLB meshes (MNI world mm, RAS: x=Right, y=Anterior, z=Superior) from label volumes.

Vertices = NIfTI affine applied to voxel coordinates, nothing else. Lateralised structures are
split at world x=0 (x<0 -> L, x>=0 -> R).
"""
import numpy as np
import nibabel as nib
import trimesh
from scipy import ndimage as ndi
from skimage import measure
from common import DATA, MESH_DIR, structures

MAX_FACES = 20000


def mask_to_mesh(mask, affine, sigma=0.8):
    pad = 3
    idx = np.argwhere(mask)
    lo = np.maximum(idx.min(0) - pad, 0); hi = idx.max(0) + pad + 1
    sl = tuple(slice(a, b) for a, b in zip(lo, hi))
    sub = ndi.gaussian_filter(mask[sl].astype(np.float32), sigma)
    verts, faces, _, _ = measure.marching_cubes(np.pad(sub, 1), 0.5)
    verts = verts - 1 + lo                                  # back to full-grid voxel coords
    world = nib.affines.apply_affine(affine, verts)
    m = trimesh.Trimesh(world, faces, process=True)
    if np.linalg.det(affine[:3, :3]) < 0:
        m.invert()                                          # keep outward winding
    trimesh.smoothing.filter_taubin(m, lamb=0.5, nu=-0.53, iterations=15)
    if len(m.faces) > MAX_FACES:
        m = m.simplify_quadric_decimation(face_count=MAX_FACES)
    m.fix_normals()
    if m.volume < 0:
        m.invert()
    return m


# Per-layer Gaussian sigma before marching cubes (thin sheets like dura/sulci need less smoothing).
SIGMA = {"anatomy": 0.8, "sulci": 0.6, "dura": 0.6}


def build_layer(layer, label_file):
    img = nib.load(str(DATA / label_file))
    lab = np.asarray(img.dataobj)
    A = img.affine
    xs = A[0, 0] * np.arange(lab.shape[0]) + A[0, 3]
    left = np.broadcast_to((xs < 0)[:, None, None], lab.shape)
    summary = []
    for s in structures():
        if s["layer"] != layer:
            continue
        base = lab == s["labelValue"]
        for fn in s["meshes"]:
            if s.get("lateralised"):
                m = base & (left if fn.endswith("_L.glb") else ~left)
            else:
                m = base
            mesh = mask_to_mesh(m, A, sigma=SIGMA.get(layer, 0.8))
            mesh.export(MESH_DIR / fn)
            c = mesh.centroid
            summary.append((fn, len(mesh.faces), (MESH_DIR / fn).stat().st_size, c))
    return summary


def main():
    import json
    vols = json.loads((DATA / "volumes.json").read_text(encoding="utf8"))
    out = []
    for L in vols["labelLayers"]:
        if (DATA / L["file"]).exists():
            out += build_layer(L["id"], L["file"])
        else:
            print(f"skip layer {L['id']}: {L['file']} not built yet")
    tot = 0
    for fn, nf, sz, c in out:
        tot += sz
        print(f"{fn:28s} faces={nf:6d} {sz/1e3:7.0f} KB  centroid=({c[0]:6.1f},{c[1]:6.1f},{c[2]:6.1f})")
    print("total mesh MB: %.2f" % (tot / 1e6))


if __name__ == "__main__":
    main()

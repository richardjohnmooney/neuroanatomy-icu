"""Shared paths/helpers for the pipeline."""
import json
from pathlib import Path
import numpy as np
import nibabel as nib

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
DATA = ROOT.parent / "public" / "data"
MESH_DIR = DATA / "meshes"
CACHE.mkdir(exist_ok=True); MESH_DIR.mkdir(parents=True, exist_ok=True)

GRID_SHAPE = (182, 218, 182)


def structures():
    return json.loads((DATA / "structures.json").read_text(encoding="utf-8"))["structures"]


def grid_affine():
    """Output grid affine = FSL MNI152 1 mm grid as stored in the Harvard-Oxford 1 mm atlas
    (RAS+ orientation, origin (-91,-126,-72), 182x218x182)."""
    from nilearn import datasets
    a = datasets.fetch_atlas_harvard_oxford(
        "sub-maxprob-thr25-1mm", data_dir=str(CACHE / "nilearn"), verbose=0)
    return np.asarray(a.maps.affine, float)


def resample_to_grid(img, order, cval=0.0, dtype=np.float32):
    """Resample a nibabel image (any affine, MNI space) to the output grid.
    order=1 linear (images), order=0 nearest (labels)."""
    from scipy.ndimage import affine_transform
    M = np.linalg.inv(img.affine) @ grid_affine()   # output voxel -> input voxel
    data = np.asarray(img.dataobj).astype(np.float32)
    out = affine_transform(data, M[:3, :3], M[:3, 3], output_shape=GRID_SHAPE,
                           order=order, mode="constant", cval=cval)
    return out.astype(dtype)


def save_nifti(arr, path, dtype):
    img = nib.Nifti1Image(arr.astype(dtype), grid_affine())
    img.set_data_dtype(dtype)
    img.header.set_xyzt_units("mm")
    nib.save(img, str(path))

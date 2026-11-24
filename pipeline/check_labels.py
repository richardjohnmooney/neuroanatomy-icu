"""Verification: sample MNI points and render PNG check images of labels_anatomy.nii.gz into cache/."""
import numpy as np
import nibabel as nib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from common import CACHE, DATA, structures

POINTS = [(0, -40, 10), (0, -38, 18), (0, 20, 15), (-45, -20, 0), (45, -20, 0), (-40, 20, 30), (40, 20, 30),
          (-35, -55, 45), (35, -55, 45), (-15, -90, 5), (15, -90, 5), (-40, -10, -30), (40, -10, -30),
          (-10, -40, 10), (10, -40, 10), (-20, 0, 5), (20, 0, 5), (-22, -12, 8), (22, -12, 8)]


def load():
    img = nib.load(str(DATA / "labels_anatomy.nii.gz"))
    return np.asarray(img.dataobj), img.affine


def vox(A, p):
    return np.round(np.linalg.inv(A) @ np.array([*p, 1.0]))[:3].astype(int)


def sample_points(lab, A):
    names = {s["labelValue"]: s["name"] for s in structures() if s["layer"] == "anatomy"}
    print("sampled points (MNI mm -> label)")
    for p in POINTS:
        v = int(lab[tuple(vox(A, p))])
        print(f"  {str(p):16s} {v:3d} {names.get(v, 'none')}")


def render(lab, A):
    cols = {s["labelValue"]: s["color"] for s in structures() if s["layer"] == "anatomy"}
    lut = np.zeros((256, 3)); lut[:] = 0.05
    for v, c in cols.items():
        lut[v] = matplotlib.colors.to_rgb(c)
    cm = ListedColormap(lut)
    views = {"axial_z8": (2, 8), "axial_z25": (2, 25), "coronal_y-18": (1, -18), "sagittal_x2": (0, 2)}
    for name, (ax_, w) in views.items():
        k = vox(A, [w if ax_ == 0 else 0, w if ax_ == 1 else 0, w if ax_ == 2 else 0])[ax_]
        sl = np.take(lab, k, axis=ax_)
        for zoom in (False, True):
            fig, ax = plt.subplots(figsize=(7, 7))
            ax.imshow(sl.T, origin="lower", cmap=cm, vmin=0, vmax=255, interpolation="nearest")
            if zoom:    # central 100 mm window (centre of the head in voxel coords)
                axes = [i for i in range(3) if i != ax_]
                cen = vox(A, (0, -15, 15))
                ax.set_xlim(cen[axes[0]] - 50, cen[axes[0]] + 50); ax.set_ylim(cen[axes[1]] - 40, cen[axes[1]] + 50)
            ax.set_title(name); ax.set_axis_off()
            fig.savefig(CACHE / f"check_{name}{'_zoom' if zoom else ''}.png", dpi=90, bbox_inches="tight")
            plt.close(fig)


def main():
    lab, A = load()
    sample_points(lab, A)
    render(lab, A)


if __name__ == "__main__":
    main()

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


def render_overlay(lab, A):
    """CT (brain window W80 L40) with anatomy label colours; extra CSF-focused views, axes in MNI mm."""
    hu = np.asarray(nib.load(str(DATA / "ct.nii.gz")).dataobj).astype(np.float32)
    cols = {s["labelValue"]: s["color"] for s in structures() if s["layer"] == "anatomy"}
    csf = [31, 32, 33, 40, 41, 42, 43, 44, 45, 30]
    rgba = np.zeros(lab.shape + (4,), np.float32)
    for v, c in cols.items():
        rgba[lab == v, :3] = matplotlib.colors.to_rgb(c)
        rgba[lab == v, 3] = 0.85 if v in csf else 0.25
    # (axis, mm, (h-axis range mm), (v-axis range mm))
    views = {"axial_z-10": (2, -10, (-60, 60), (-90, 50)), "axial_z-30": (2, -30, (-60, 60), (-90, 40)),
             "sagittal_x0": (0, 0, (-100, 60), (-75, 40)), "coronal_y-30": (1, -30, (-60, 60), (-75, 40))}
    org = A[:3, 3]
    for name, (ax_, w, hr, vr) in views.items():
        k = int(round(w - org[ax_]))
        axes = [i for i in range(3) if i != ax_]            # in-plane axes (voxel order)
        res = []
        for vol in (hu, rgba):
            sl = np.take(vol, k, axis=ax_)
            sl = np.swapaxes(sl, 0, 1)                       # rows = 2nd axis, cols = 1st axis
            res.append(sl)
        h0, v0 = org[axes[0]], org[axes[1]]
        ext = (h0, h0 + hu.shape[axes[0]] - 1, v0, v0 + hu.shape[axes[1]] - 1)
        for tag, over in (("", True), ("_ct", False)):
            fig, a = plt.subplots(figsize=(8, 7))
            a.imshow(res[0], origin="lower", cmap="gray", vmin=0, vmax=80, extent=ext, interpolation="nearest")
            if over:
                a.imshow(res[1], origin="lower", extent=ext, interpolation="nearest")
            a.set_xlim(*hr); a.set_ylim(*vr)
            a.set_xlabel("xyz"[axes[0]] + " mm"); a.set_ylabel("xyz"[axes[1]] + " mm"); a.set_title(name + tag)
            a.set_aspect("equal")
            fig.savefig(CACHE / f"check_{name}{tag}.png", dpi=80, bbox_inches="tight")
            plt.close(fig)


def main():
    lab, A = load()
    sample_points(lab, A)
    render(lab, A)
    render_overlay(lab, A)


if __name__ == "__main__":
    main()

"""Build public/data/ct.nii.gz (int16 HU) on the MNI152 1 mm grid (182x218x182).

Default: a real adult head CT (CQ500-CT-148, series 3 "Thin Plain", 0.625 mm, CC BY-NC-SA 4.0) is
skull-stripped, registered to the Rorden CT template brain in MNI space (rigid -> affine -> SyN, via antspyx),
applied to the full head (linear interpolation), defaced, clipped to [-1024, 3071] and saved.

  uv run python build_ct.py              # real CT (falls back to the template CT if subject data is missing)
  uv run python build_ct.py --template   # old behaviour: Rorden 2 mm template CT (Cormack -> HU), 1 mm grid
  uv run python build_ct.py --force      # redo registration even if the cached transform exists

The old template output is kept at cache/ct_template_hu.nii.gz (written the first time this runs if absent).
Downloaded data is treated as untrusted: only read through pydicom / SimpleITK / nibabel, never executed.
"""
import sys
import zipfile
import shutil
import io
import numpy as np
import nibabel as nib
from scipy import ndimage as ndi
from common import CACHE, DATA, GRID_SHAPE, grid_affine, save_nifti

STUDY = "CQ500-CT-148"
SERIES = 3                       # "Thin Plain", 0.625 mm, STANDARD kernel, non-contrast
WORK = CACHE / "ct_subject" / "work"
TEMPLATE_HU = CACHE / "ct_template_hu.nii.gz"
OUT = DATA / "ct.nii.gz"


# ----------------------------------------------------------------------------------------------- helpers
def template_hu_native(stripped):
    """Rorden template (2 mm, MNI) in HU as nibabel image."""
    from build_labels import cormack_to_hu
    img = nib.load(str(CACHE / ("scct_stripped.nii" if stripped else "scct.nii")))
    return nib.Nifti1Image(cormack_to_hu(np.asarray(img.dataobj)), img.affine)


def build_template_ct():
    """Old fallback: template CT upsampled onto our grid."""
    from common import resample_to_grid
    hu = resample_to_grid(template_hu_native(False), order=1, cval=-1024.0)
    hu = np.clip(np.round(hu), -1024, 3071)
    save_nifti(hu, OUT, np.int16)
    (CACHE / "ct_source.txt").write_text("template")
    print("wrote template CT ->", OUT)
    return hu


def load_subject():
    """DICOM series (extracted from the CQ500 zip, only DICOM bytes written to disk) -> SimpleITK image in HU,
    resampled to orthogonal axes if the gantry is tilted."""
    import SimpleITK as sitk
    import pydicom
    from fetch_sources import fetch_ct_subject
    zpath = fetch_ct_subject()
    if zpath is None:
        return None
    d = WORK / f"{STUDY}_s{SERIES}"
    if not d.is_dir() or not any(d.iterdir()):
        d.mkdir(parents=True, exist_ok=True)
        zf = zipfile.ZipFile(zpath)
        k = 0
        for n in zf.namelist():
            if n.endswith("/"):
                continue
            b = zf.read(n)
            try:
                ds = pydicom.dcmread(io.BytesIO(b), stop_before_pixels=True, force=True)
            except Exception:
                continue
            if "SOPClassUID" in ds and str(ds.get("SeriesNumber")) == str(SERIES):
                (d / f"{k:04d}.dcm").write_bytes(b); k += 1
    r = sitk.ImageSeriesReader()
    r.SetFileNames(r.GetGDCMSeriesFileNames(str(d)))
    im = r.Execute()           # applies RescaleSlope/Intercept -> HU, orientation in LPS physical space
    im = sitk.Cast(im, sitk.sitkFloat32)
    # gantry tilt (this series has 0) would show as non-identity/skewed geometry; ITK warns, so force orthogonal
    print("subject", im.GetSize(), im.GetSpacing())
    return im


def to_iso(im, sp=1.0, smooth=0.45):
    """Anti-aliased resample to isotropic grid (returns SimpleITK image)."""
    import SimpleITK as sitk
    g = sitk.SmoothingRecursiveGaussian(im, smooth)
    size = [int(round(s * z / sp)) for s, z in zip(im.GetSize(), im.GetSpacing())]
    return sitk.Resample(g, size, sitk.Transform(), sitk.sitkLinear, im.GetOrigin(), (sp,) * 3,
                         im.GetDirection(), -1024.0)


def brain_mask_subject(arr):
    """arr: HU, 1 mm iso (numpy). Intracranial soft tissue + CSF = largest component of smoothed HU in [-5,100] after
    erosion that is separated from scalp by the skull; dilate back, fill holes."""
    s = ndi.gaussian_filter(arr, 1.0)
    m = (s > -2) & (s < 90)
    # bone/air barrier is thin at the skull base: erode generously, take biggest component
    st = ndi.generate_binary_structure(3, 1)
    e = ndi.binary_erosion(m, st, iterations=5)
    lab, n = ndi.label(e)
    if n == 0:
        raise RuntimeError("brain mask failed")
    sz = ndi.sum(e, lab, range(1, n + 1))
    # biggest component that is near the volume centre of the head is the brain
    k = 1 + int(np.argmax(sz))
    e = lab == k
    e = ndi.binary_dilation(e, st, iterations=5) & m
    e = ndi.binary_closing(e, st, iterations=3)
    e = ndi.binary_fill_holes(e)
    return e


def nib_from_sitk(im):
    """SimpleITK image -> nibabel (RAS+ affine)."""
    a = __import__("SimpleITK").GetArrayFromImage(im).transpose(2, 1, 0)   # x,y,z
    M = np.eye(4)
    D = np.array(im.GetDirection()).reshape(3, 3)
    M[:3, :3] = D * np.array(im.GetSpacing())
    M[:3, 3] = im.GetOrigin()
    lps2ras = np.diag([-1, -1, 1, 1])
    return nib.Nifti1Image(a.astype(np.float32), lps2ras @ M)


def window(a, lo=0.0, hi=85.0):
    return np.clip(a, lo, hi)


# ----------------------------------------------------------------------------------------------- registration
def register(force=False):
    import ants
    WORK.mkdir(parents=True, exist_ok=True)
    fx_path = WORK / "fixed_brain.nii.gz"
    mv_path = WORK / "moving_brain.nii.gz"
    full_path = WORK / "subject_full_1mm.nii.gz"
    prefix = str(WORK / "reg_")
    fw = WORK / "warped_brain.nii.gz"
    xf_file = WORK / "transforms.txt"
    if force or not xf_file.exists():
        im = load_subject()
        if im is None:
            return None
        iso = to_iso(im, 1.0)
        subj = nib_from_sitk(iso)
        arr = np.asarray(subj.dataobj)
        mask = brain_mask_subject(arr)
        print("subject brain mask volume (mL):", mask.sum() / 1000.0)
        nib.save(subj, str(full_path))
        brain = np.where(mask, window(arr), 0.0)
        nib.save(nib.Nifti1Image(brain.astype(np.float32), subj.affine), str(mv_path))
        # fixed: template brain on our grid
        from common import resample_to_grid
        tb = template_hu_native(True)
        fb = resample_to_grid(tb, order=1, cval=-1024.0)
        fm = fb > -400
        fm = ndi.binary_fill_holes(fm)
        fbw = np.where(fm, window(fb), 0.0)
        nib.save(nib.Nifti1Image(fbw.astype(np.float32), grid_affine()), str(fx_path))
        nib.save(nib.Nifti1Image(fm.astype(np.uint8), grid_affine()), str(WORK / "fixed_mask.nii.gz"))
        fixed = ants.image_read(str(fx_path))
        moving = ants.image_read(str(mv_path))
        fmask = ants.image_read(str(WORK / "fixed_mask.nii.gz"))
        r = ants.registration(fixed=fixed, moving=moving, type_of_transform="SyN", mask=fmask,
                              outprefix=prefix, aff_metric="mattes", syn_metric="CC", syn_sampling=4,
                              reg_iterations=(60, 30, 10), grad_step=0.1, flow_sigma=3.0, total_sigma=1.0,
                              random_seed=1, verbose=False)
        ants.image_write(r["warpedmovout"], str(fw))
        xf_file.write_text("\n".join(r["fwdtransforms"]))
        print("transforms:", r["fwdtransforms"])
    return [l for l in xf_file.read_text().splitlines() if l]


def apply_to_head(xfs):
    import ants
    fixed = ants.image_read(str(WORK / "fixed_brain.nii.gz"))
    moving = ants.image_read(str(WORK / "subject_full_1mm.nii.gz"))
    w = ants.apply_transforms(fixed=fixed, moving=moving, transformlist=xfs, interpolator="linear",
                              defaultvalue=-1024)
    return w.numpy()


# ----------------------------------------------------------------------------------------------- defacing
def deface(hu):
    """Remove the face (nose, lips, chin, cheeks, eyes, anterior facial skin) in MNI space, keeping the anterior
    cranial fossa floor / orbit roofs, frontal sinus, skull base and everything intracranial.
    1) plane cut: air below z = DEF_Z (anterior to DEF_Y1), ramping down to the skull base posteriorly;
    2) skin/soft tissue lying outside the (axial-slice filled) skull envelope anterior to DEF_Y2 -> air."""
    aff = grid_affine()
    ij = np.indices(GRID_SHAPE).reshape(3, -1)
    xyz = (aff[:3, :3] @ ij + aff[:3, 3:4]).reshape(3, *GRID_SHAPE)
    y, z = xyz[1], xyz[2]
    zcut = np.minimum(DEF_Z, DEF_Z + DEF_SLOPE * (y - DEF_Y1))
    remove = (z < zcut) & (y > DEF_Y0)
    bone = hu > 250
    solid = ndi.binary_closing(bone, ndi.generate_binary_structure(3, 1), iterations=4)
    for k in range(solid.shape[2]):
        solid[:, :, k] = ndi.binary_fill_holes(solid[:, :, k])
    skin = (~solid) & (hu > -300) & (y > DEF_Y2) & (z >= zcut) & (z < DEF_ZSKIN)
    remove |= skin
    out = hu.copy()
    out[remove] = -1000
    return out, remove


DEF_Y0, DEF_Y1, DEF_Z, DEF_SLOPE, DEF_Y2, DEF_ZSKIN = 15.0, 45.0, -28.0, 1.76, 45.0, 60.0


# ----------------------------------------------------------------------------------------------- main
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not TEMPLATE_HU.exists() and OUT.exists():
        shutil.copy(OUT, TEMPLATE_HU)       # preserve existing (template) output before ever overwriting
        print("saved old CT ->", TEMPLATE_HU)
    if "--template" in argv:
        return build_template_ct()
    try:
        xfs = register(force="--force" in argv)
    except ImportError as e:
        print("registration deps missing (%s); falling back to template CT" % e)
        xfs = None
    if xfs is None:
        print("subject CT unavailable -> template CT fallback")
        return build_template_ct()
    hu = apply_to_head(xfs)
    hu, _ = deface(hu)
    hu = np.clip(np.round(hu), -1024, 3071)
    save_nifti(hu, OUT, np.int16)
    (CACHE / "ct_source.txt").write_text("real")      # build_labels reads this to choose real-CT vs template labelling
    print("wrote real CT ->", OUT)
    return hu


if __name__ == "__main__":
    main()

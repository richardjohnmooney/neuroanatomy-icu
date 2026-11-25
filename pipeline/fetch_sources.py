"""Download source data (data only; never executed) into pipeline/cache/."""
import requests
from common import CACHE

CT_BASE = "https://github.com/neurolabusc/Clinical/raw/master/"
FILES = ["scct.nii", "scct_stripped.nii"]


def fetch_ct():
    for f in FILES:
        p = CACHE / f
        if p.exists() and p.stat().st_size > 1000:
            print("cached", p.name); continue
        r = requests.get(CT_BASE + f, timeout=120); r.raise_for_status()
        p.write_bytes(r.content); print("downloaded", f, len(r.content))


# Real head CT: CQ500 (Qure.ai, Chilamkurthy et al. 2018, Lancet 392:2388; arXiv:1803.05854), CC BY-NC-SA 4.0.
# The only public distribution is a 28.7 GB BitTorrent (Academic Torrents, no login) of one zip per study.
# We fetch just reads.csv + the single study zip with python-libtorrent (selective file priorities); the zip is
# untrusted data and is only ever read through pydicom/SimpleITK (see build_ct.py). Needs `libtorrent`
# (`uv pip install libtorrent`); returns None if the zip is absent and cannot be fetched.
CQ500_TORRENT = "https://academictorrents.com/download/47e9d8aab761e75fd0a81982fa62bddf3a173831.torrent"


def fetch_ct_subject(study="CQ500-CT-148", timeout=1800):
    import time
    d = CACHE / "ct_subject"
    z = d / "qure.headct.study" / f"{study}.zip"
    if z.is_file() and z.stat().st_size > 1_000_000:
        return z
    try:
        import libtorrent as lt
    except ImportError:
        print("CQ500 subject zip missing and python 'libtorrent' not installed (uv pip install libtorrent)")
        return None
    d.mkdir(exist_ok=True)
    tf = d / "cq500.torrent"
    if not tf.exists():
        r = requests.get(CQ500_TORRENT, timeout=120); r.raise_for_status(); tf.write_bytes(r.content)
    ti = lt.torrent_info(str(tf)); fs = ti.files()
    ses = lt.session({"listen_interfaces": "0.0.0.0:6881"})
    p = lt.add_torrent_params(); p.ti = ti; p.save_path = str(d)
    h = ses.add_torrent(p)
    want = [i for i in range(ti.num_files())
            if fs.file_path(i).replace("\\", "/").endswith((f"/{study}.zip", "/reads.csv"))]
    h.prioritize_files([7 if i in want else 0 for i in range(ti.num_files())])
    t0 = time.time()
    while time.time() - t0 < timeout:
        fp = h.file_progress()
        if all(fp[i] >= fs.file_size(i) for i in want):
            break
        time.sleep(5)
    ses.remove_torrent(h)
    return z if z.is_file() else None


def fetch_atlases():
    from nilearn import datasets
    for lvl in ("lobe", "hemisphere", "gyrus"):
        datasets.fetch_atlas_talairach(lvl, data_dir=str(CACHE / "nilearn"))
    for a in ("cort-maxprob-thr25-1mm", "sub-maxprob-thr25-1mm"):
        datasets.fetch_atlas_harvard_oxford(a, data_dir=str(CACHE / "nilearn"))


# Liu C-F et al. 2023, Sci Data 10:74 (doi 10.1038/s41597-022-01923-0), "Digital 3D Brain MRI Arterial
# Territories Atlas"; NITRC project "arterialatlas", licence "Attribution" (CC BY; paper CC BY 4.0).
ARTERIAL_URL = "https://www.nitrc.org/frs/download.php/12382/Atlas_MNI152.zip"


def fetch_arterial_atlas():
    """Locate the locally present Liu et al. 2023 atlas (FSL MNI152 182x218x182 version). NITRC downloads are
    login-gated, so the user downloaded them manually into cache/Atlas_182_MNI152/ (data only, never executed).
    Returns that directory if ArterialAtlas.nii and ArterialAtlas_level2.nii are present, else None."""
    for d in (CACHE / "Atlas_182_MNI152", CACHE / "arterial" / "Atlas_182_MNI152"):
        if (d / "ArterialAtlas.nii").is_file() and (d / "ArterialAtlas_level2.nii").is_file():
            print("arterial atlas found locally:", d)
            return d
    print("arterial atlas: not found locally (NITRC download is login-gated; download Atlas_MNI152.zip from "
          "https://www.nitrc.org/projects/arterialatlas/ and extract the 182x218x182 folder to cache/Atlas_182_MNI152/)")
    return None


def main():
    fetch_ct(); fetch_atlases()


if __name__ == "__main__":
    main()

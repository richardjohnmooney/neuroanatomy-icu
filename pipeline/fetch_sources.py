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
    """Try the NITRC download. NITRC serves a login page (HTML) instead of the zip without an account, in which
    case this returns None (we do not bypass the login gate). Data only; never executed."""
    p = CACHE / "arterial" / "Atlas_MNI152.zip"
    p.parent.mkdir(exist_ok=True)
    if p.exists() and p.read_bytes()[:2] == b"PK":
        return p
    try:
        r = requests.get(ARTERIAL_URL, timeout=120)
        if r.ok and r.content[:2] == b"PK":
            p.write_bytes(r.content); return p
    except requests.RequestException as e:
        print("arterial atlas fetch failed:", e)
    print("arterial atlas: NITRC download is login-gated (got HTML, not a zip)")
    return None


def main():
    fetch_ct(); fetch_atlases()


if __name__ == "__main__":
    main()

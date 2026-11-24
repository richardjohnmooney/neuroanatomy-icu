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


def main():
    fetch_ct(); fetch_atlases()


if __name__ == "__main__":
    main()

"""Run the full pipeline in order. Add new volumes/layers as functions in the build_* modules."""
import fetch_sources, build_ct, build_labels, build_overlays, build_territories, build_vessels, build_cta, build_meshes, check_labels

if __name__ == "__main__":
    fetch_sources.main()
    build_ct.main([])        # public/data/ct.nii.gz: real CT warped to MNI (falls back to the Rorden template)
    build_labels.main()      # labels_anatomy (lobes, deep grey, brainstem, CSF)
    build_overlays.main()    # labels_sulci, labels_dura (read labels_anatomy)
    build_territories.main() # labels_territories (arterial territory atlas)
    build_vessels.main()     # labels_vessels + tube meshes (vessels_mni.yaml, dura labels)
    build_cta.main()         # cta.nii.gz + ctv.nii.gz (simulated contrast phases from ct + vessel centrelines)
    build_meshes.main()
    check_labels.main()      # sampled points + cache/check_*.png

"""Run the full pipeline in order. Add new volumes/layers as functions in the build_* modules."""
import fetch_sources, build_labels, build_overlays, build_meshes, check_labels

if __name__ == "__main__":
    fetch_sources.main()
    build_labels.main()      # labels_anatomy (lobes, deep grey, brainstem, CSF)
    build_overlays.main()    # labels_sulci, labels_dura (read labels_anatomy)
    build_meshes.main()
    check_labels.main()      # sampled points + cache/check_*.png

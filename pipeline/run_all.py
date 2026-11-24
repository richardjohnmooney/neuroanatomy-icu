"""Run the full pipeline in order. Add new volumes/layers as functions in the build_* modules."""
import fetch_sources, build_labels, build_meshes, check_labels

if __name__ == "__main__":
    fetch_sources.main()
    build_labels.main()
    build_meshes.main()
    check_labels.main()      # sampled points + cache/check_*.png

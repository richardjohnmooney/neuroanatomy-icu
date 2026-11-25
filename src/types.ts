// Shared data contracts. public/data/*.json must conform to these.
// Coordinates everywhere are MNI152 world millimetres, RAS (+x = patient Right, +y = Anterior, +z = Superior).

export type Vec3 = [number, number, number];
export type Plane = 'axial' | 'coronal' | 'sagittal';
export type Modality = 'CT' | 'T1' | 'T2' | 'FLAIR';

/** public/data/volumes.json -> VolumesManifest */
export interface BaseVolume {
  id: string;               // e.g. "ct"
  label: string;            // UI label, e.g. "Non-contrast CT"
  modality: Modality;
  file: string;             // relative to public/data, e.g. "ct.nii.gz"
  windows: WindowPreset[];  // first is default
  hideGroups?: string[];    // structure groups switched off by default while this scan is shown (e.g. veins on a CTA)
  attribution: string;      // short source + licence
}
export interface WindowPreset {
  id: string;    // "brain"
  label: string; // "Brain (W80 L40)"
  width: number;
  level: number;
}
export interface LabelLayer {
  id: string;     // "anatomy" | "territories" | "dura" | "vessels" ...
  label: string;
  file: string;   // e.g. "labels_anatomy.nii.gz" (uint8, same grid as base volumes)
}
export interface VolumesManifest {
  grid: { shape: Vec3; voxelMm: number }; // all volumes share this grid/affine
  baseVolumes: BaseVolume[];
  labelLayers: LabelLayer[];
}

/** public/data/structures.json -> StructuresManifest */
export interface Structure {
  id: string;            // stable kebab-case, e.g. "frontal-lobe"
  name: string;          // "Frontal lobe"
  group: string;         // groupId
  layer: string;         // LabelLayer.id the structure is rasterised in
  labelValue: number;    // voxel value in that layer (1..255, unique per layer)
  color: string;         // "#rrggbb"
  meshes: string[];      // files in public/data/meshes/, e.g. ["frontal-lobe_L.glb","frontal-lobe_R.glb"]; [] if none
  lateralised: boolean;  // true => hover readout appends (L)/(R) from x sign
  notes: string;         // teaching text (markdown-lite). Flagged for clinical review.
  ctNotes?: string;      // modality-specific tips
  mriNotes?: string;
}
export interface Group { id: string; name: string; }
export interface Topic {
  id: string;
  name: string;
  groups: string[];       // groups visible when topic active
  focus?: Vec3;           // default crosshair, MNI mm
  plane?: Plane;          // default plane for this topic
  scan?: string;          // default BaseVolume.id for this topic (else the first base volume)
  defaultHidden?: string[]; // groups listed in `groups` but not coloured on the slices when the topic opens (3D unaffected)
  /** Context groups: listed after `groups`, off on the slices and faint (GHOST_OPACITY) in 3D by default,
   *  so the topic's own structures stand out. */
  context?: string[];
  /** Default 3D opacity per group id for this topic (overrides 1 / GHOST_OPACITY), e.g. territories 0.25. */
  opacity3d?: Record<string, number>;
  intro: string;          // shown in info panel when no structure selected
}
export interface StructuresManifest {
  reviewed: boolean;      // false until clinician signs off teaching text
  groups: Group[];
  topics: Topic[];
  structures: Structure[];
}

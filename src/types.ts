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
  /** Tutorial id (tutorials.json) offered from this topic's notes pane. */
  tutorial?: string;
  intro: string;          // shown in info panel when no structure selected
}
export interface StructuresManifest {
  reviewed: boolean;      // false until clinician signs off teaching text
  groups: Group[];
  topics: Topic[];
  structures: Structure[];
}

/** 3D camera request: look at `target` (MNI mm, default brain centre) from direction `dir`
 *  (MNI RAS vector from target towards the camera, e.g. [1,0,0] = from patient right).
 *  `zoom` > 1 moves closer than the whole-brain fit distance. */
export interface CameraView { dir: Vec3; target?: Vec3; zoom?: number; }

/** Arrow annotation drawn on the slices (projected into the plane) and in 3D. MNI mm. */
export interface Arrow {
  from: Vec3;
  to: Vec3;
  color?: string;          // default white
  label?: string;          // drawn at the tail on the slices
  planes?: Plane[];        // slice planes that show it (default all); 3D always shows it unless `no3d`
  depth?: number;          // shown on a slice only within this many mm of it (default 12)
  no3d?: boolean;
}

/** public/data/tutorials.json. Steps are cumulative: a field left out keeps the previous step's
 *  value (except title/caption/notes/arrows/question/select, which belong to one step). */
export interface TutorialStep {
  title: string;
  caption: string;         // one or two short lines for the audience
  notes?: string;          // speaker notes (presenter only)
  topic?: string;          // topic whose structure list the sidebar shows
  scan?: string;           // BaseVolume id
  window?: string;         // WindowPreset id (default: the scan's first)
  plane?: Plane;
  layout?: 'single' | 'multi';
  crosshair?: Vec3;        // the player glides here
  show?: string[];         // structure or group ids coloured on the slices (replaces)
  show3d?: Record<string, number>; // structure or group id -> 3D opacity (replaces); later keys win
  explode?: number;
  clip3d?: boolean;
  slicePlane3d?: boolean;  // show the 3D slice-plane marker (default true)
  overlayOpacity?: number;
  camera?: CameraView;
  select?: string;         // structure selected (highlighted) for this step
  arrows?: Arrow[];
  question?: TutorialQuestion;
}
export interface TutorialQuestion {
  prompt: string;          // replaces the caption until answered
  answer: string[];        // structure ids accepted (click it in any panel)
  reveal: string;          // shown once answered or revealed
  revealArrows?: Arrow[];  // added on reveal
}
export interface Tutorial { id: string; name: string; description: string; steps: TutorialStep[]; }
export interface TutorialsManifest { tutorials: Tutorial[]; }

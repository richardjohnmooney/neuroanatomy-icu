// Loads the JSON manifests once; every panel receives the same AppData.
import type { BaseVolume, StructuresManifest, Structure, Topic, Tutorial, TutorialsManifest, VolumesManifest, Vec3 } from './types';
import { GHOST_OPACITY } from './state';

export interface AppData {
  volumes: VolumesManifest;
  manifest: StructuresManifest;
  byId: Map<string, Structure>;
  /** layerId -> labelValue -> Structure */
  byLabel: Map<string, Map<number, Structure>>;
  tutorials: Tutorial[];
  /** URL of a file in public/data (respects Vite base). */
  url(file: string): string;
}

const base = import.meta.env.BASE_URL + 'data/';
/** Data file URL with the build's data version, so a new deploy never mixes cached and fresh files. */
const versioned = (f: string) => `${base}${f}?v=${__DATA_VERSION__}`;

export async function loadData(): Promise<AppData> {
  const [volumes, manifest, tut] = await Promise.all([
    fetch(versioned('volumes.json')).then((r) => r.json() as Promise<VolumesManifest>),
    fetch(versioned('structures.json')).then((r) => r.json() as Promise<StructuresManifest>),
    fetch(versioned('tutorials.json'))
      .then((r) => (r.ok ? r.json() as Promise<TutorialsManifest> : { tutorials: [] }))
      .catch(() => ({ tutorials: [] } as TutorialsManifest)),
  ]);
  const byId = new Map(manifest.structures.map((s) => [s.id, s]));
  const byLabel = new Map<string, Map<number, Structure>>();
  for (const s of manifest.structures) {
    if (!byLabel.has(s.layer)) byLabel.set(s.layer, new Map());
    byLabel.get(s.layer)!.set(s.labelValue, s);
  }
  return { volumes, manifest, byId, byLabel, tutorials: tut.tutorials ?? [], url: versioned };
}

/** Patient side from MNI x (RAS: +x is patient Right). */
export function sideOf(p: Vec3): 'L' | 'R' {
  return p[0] < 0 ? 'L' : 'R';
}

/** Display name with side suffix for lateralised structures. */
export function displayName(s: Structure, p?: Vec3): string {
  return s.lateralised && p ? `${s.name} (${sideOf(p)})` : s.name;
}

/** All groups listed for a topic: its own groups, then its context groups. */
export function topicGroups(t: Topic): string[] {
  return [...new Set([...t.groups, ...(t.context ?? [])])];
}

/** Structures listed for a topic (own + context groups). */
export function topicStructures(data: AppData, topicId: string): Structure[] {
  const t = data.manifest.topics.find((x) => x.id === topicId);
  if (!t) return [];
  const gs = topicGroups(t);
  return data.manifest.structures.filter((s) => gs.includes(s.group));
}

/** Default CT (slice) colouring and 3D opacity for a group in a topic, for the given scan.
 *  Context and `defaultHidden` groups start off the slices; scan `hideGroups` start off both. */
export function groupDefaults(t: Topic, groupId: string, scan?: BaseVolume): { onCt: boolean; op3d: number } {
  if (scan?.hideGroups?.includes(groupId)) return { onCt: false, op3d: 0 };
  const isContext = (t.context ?? []).includes(groupId) && !t.groups.includes(groupId);
  const onCt = !isContext && !(t.defaultHidden ?? []).includes(groupId);
  const op3d = t.opacity3d?.[groupId] ?? (isContext ? GHOST_OPACITY : 1);
  return { onCt, op3d };
}

/** Expand a mix of structure ids and group ids into structure ids. Unknown ids are reported once. */
export function expandIds(data: AppData, ids: string[]): string[] {
  const out: string[] = [];
  for (const id of ids) {
    if (data.byId.has(id)) { out.push(id); continue; }
    const members = data.manifest.structures.filter((s) => s.group === id).map((s) => s.id);
    if (members.length) out.push(...members);
    else console.warn('unknown structure or group id', id);
  }
  return out;
}

// Loads the JSON manifests once; every panel receives the same AppData.
import type { StructuresManifest, Structure, VolumesManifest, Vec3 } from './types';

export interface AppData {
  volumes: VolumesManifest;
  manifest: StructuresManifest;
  byId: Map<string, Structure>;
  /** layerId -> labelValue -> Structure */
  byLabel: Map<string, Map<number, Structure>>;
  /** URL of a file in public/data (respects Vite base). */
  url(file: string): string;
}

const base = import.meta.env.BASE_URL + 'data/';

export async function loadData(): Promise<AppData> {
  const [volumes, manifest] = await Promise.all([
    fetch(base + 'volumes.json').then((r) => r.json() as Promise<VolumesManifest>),
    fetch(base + 'structures.json').then((r) => r.json() as Promise<StructuresManifest>),
  ]);
  const byId = new Map(manifest.structures.map((s) => [s.id, s]));
  const byLabel = new Map<string, Map<number, Structure>>();
  for (const s of manifest.structures) {
    if (!byLabel.has(s.layer)) byLabel.set(s.layer, new Map());
    byLabel.get(s.layer)!.set(s.labelValue, s);
  }
  return { volumes, manifest, byId, byLabel, url: (f) => base + f };
}

/** Patient side from MNI x (RAS: +x is patient Right). */
export function sideOf(p: Vec3): 'L' | 'R' {
  return p[0] < 0 ? 'L' : 'R';
}

/** Display name with side suffix for lateralised structures. */
export function displayName(s: Structure, p?: Vec3): string {
  return s.lateralised && p ? `${s.name} (${sideOf(p)})` : s.name;
}

/** Structures that belong to the active topic. */
export function topicStructures(data: AppData, topicId: string): Structure[] {
  const t = data.manifest.topics.find((x) => x.id === topicId);
  if (!t) return [];
  return data.manifest.structures.filter((s) => t.groups.includes(s.group));
}

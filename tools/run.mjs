// Runs a dev-dependency CLI (vite, tsc) from local node_modules if present,
// otherwise from DEPS_DIR (default C:/node_modules/ct-brain-anatomy/node_modules).
// Why: npm cannot write node_modules onto the Google Drive volume, so on the dev laptop
// dependencies live on C:. In CI (GitHub Actions) a normal `npm ci` provides local node_modules.
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const bins = { vite: 'vite/bin/vite.js', tsc: 'typescript/bin/tsc' };
const [tool, ...args] = process.argv.slice(2);
const local = resolve('node_modules');
const deps = existsSync(local) ? local
  : (process.env.DEPS_DIR ?? 'C:/node_modules/ct-brain-anatomy/node_modules');
const bin = join(deps, bins[tool]);
if (!existsSync(bin)) {
  console.error(`Cannot find ${tool} at ${bin}. Run: npm run deps`);
  process.exit(1);
}
const r = spawnSync(process.execPath, [bin, ...args], { stdio: 'inherit', env: { ...process.env, DEPS_DIR: deps } });
process.exit(r.status ?? 1);

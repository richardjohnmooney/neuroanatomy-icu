// No `import 'vite'` here on purpose: dependencies may live outside the project (see tools/run.mjs).
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const local = resolve('node_modules');
const deps = existsSync(local) ? null : process.env.DEPS_DIR;

// Data version stamped into every data URL (?v=...), so each deploy fetches a matching set of
// data files instead of mixing fresh and browser-cached ones (GitHub Pages caches for 10 min).
const dataVersion = process.env.GITHUB_SHA?.slice(0, 7) ?? Date.now().toString(36);

export default {
  base: './', // relative so the build works on GitHub Pages under /<repo>/
  define: { __DATA_VERSION__: JSON.stringify(dataVersion) },
  cacheDir: deps ? resolve(deps, '.vite-ct-brain') : undefined,
  resolve: deps
    ? {
        alias: [
          { find: /^three(\/.*)?$/, replacement: resolve(deps, 'three') + '$1' },
          { find: /^@niivue\/niivue$/, replacement: resolve(deps, '@niivue/niivue') },
        ],
      }
    : {},
  server: deps ? { fs: { allow: ['.', deps] } } : {},
};

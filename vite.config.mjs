// No `import 'vite'` here on purpose: dependencies may live outside the project (see tools/run.mjs).
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const local = resolve('node_modules');
const deps = existsSync(local) ? null : process.env.DEPS_DIR;

export default {
  base: './', // relative so the build works on GitHub Pages under /<repo>/
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

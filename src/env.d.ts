// Minimal Vite env typing (avoids depending on vite/client types resolving from the project dir).
interface ImportMetaEnv { readonly BASE_URL: string; readonly DEV: boolean; }
interface ImportMeta { readonly env: ImportMetaEnv; }
/** Build-time data version (vite.config.mjs `define`), appended to data URLs as a cache buster. */
declare const __DATA_VERSION__: string;

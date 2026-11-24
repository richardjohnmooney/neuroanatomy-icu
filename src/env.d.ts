// Minimal Vite env typing (avoids depending on vite/client types resolving from the project dir).
interface ImportMetaEnv { readonly BASE_URL: string; readonly DEV: boolean; }
interface ImportMeta { readonly env: ImportMetaEnv; }

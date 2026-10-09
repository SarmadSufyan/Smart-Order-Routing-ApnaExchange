/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The gateway's address; empty = http://127.0.0.1:8000 (this machine). */
  readonly VITE_API_BASE?: string
  /** Set when the page shows another machine's gateway (the lab server): a badge names it. */
  readonly VITE_SERVER_LABEL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

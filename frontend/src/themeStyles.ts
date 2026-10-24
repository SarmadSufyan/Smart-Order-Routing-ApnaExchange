/**
 * Global theme CSS — injected once into <head>.
 *
 * Three looks, chosen with the palette button in the top bar (`html[data-style]`), each dark or light
 * (`html[data-theme]`). Every colour in the app is a `--c-*` variable (theme.ts), so one palette restyles every page:
 *
 *   studio   — the ApnaSOR design system (the default): Apple-style. Inter / San Francisco type with tabular numbers,
 *              black and grey surfaces, white as the accent, green and red the only colours (profit / loss,
 *              buy / sell), generous rounded corners, soft shadows, sentence-case labels.
 *   glass    — "Liquid Glass" by Mahnoor & Maham (branch theme/liquid-glass), kept as they designed it so the team
 *              can compare and choose.
 *   terminal — the original look: monospace, cyan accent, square corners.
 *
 * Components keep their inline styles; a few attribute-selector rules per look round corners, frost the surfaces
 * and soften labels, so no page has to change to follow the theme. Studio finds its surfaces by data attributes
 * (`data-card` on every Card, `data-glass="chrome" | "float"` on the shell and floating panels, `data-page-title`,
 * `data-card-title`); Liquid Glass frosts every `background: var(--c-surface)` as its authors did.
 */

const STUDIO = `
/* ── Studio: tokens ─────────────────────────────────────────── */
:root,
html[data-style="studio"][data-theme="dark"] {
  /* Neutral greys; white is the accent; green and red are the only colours (profit / loss, buy / sell, up / down). */
  --c-bg:       #0E0E0F;                       /* fields, the page under the cards */
  --c-surface:  rgba(255, 255, 255, 0.045);    /* a field, a tile or a row inside a card */
  --c-surface2: rgba(255, 255, 255, 0.07);     /* a chip, a quiet button */
  --c-border:   rgba(255, 255, 255, 0.10);
  --c-border2:  rgba(255, 255, 255, 0.06);
  --c-text:     #F2F2F2;
  --c-muted:    rgba(242, 242, 242, 0.62);
  --c-dim:      rgba(242, 242, 242, 0.40);
  --c-accent:   #F2F2F2;
  --c-blue:     #F2F2F2;                       /* "the accent" in the components: white, not blue */
  --c-on-accent: #0E0E0F;                      /* text on an accent background */
  --c-green:    #30D158;
  --c-red:      #FF453A;
  --c-orange:   #C7C7CC;                       /* warnings: a light grey, not a third colour */
  --c-purple:   #8E8E93;                       /* chart series: a grey ramp */
  --c-gold:     #AEAEB2;

  --card-bg:     #151516;
  --card-border: rgba(255, 255, 255, 0.07);
  --chrome-bg:   rgba(12, 12, 13, 0.88);       /* top bar and sidebar */
  --float-bg:    rgba(26, 26, 28, 0.97);       /* menus, dialogs, the copilot drawer */
  --modal-overlay: rgba(0, 0, 0, 0.60);
  --shadow-card: 0 1px 0 rgba(255, 255, 255, 0.03) inset, 0 8px 24px rgba(0, 0, 0, 0.35);
  --shadow-pop:  0 1px 0 rgba(255, 255, 255, 0.05) inset, 0 28px 72px rgba(0, 0, 0, 0.60);
  --focus-ring:  0 0 0 3px rgba(255, 255, 255, 0.16);
  --selection:   rgba(255, 255, 255, 0.22);
  --page-bg:     #0A0A0B;

  color-scheme: dark;
}

html[data-style="studio"][data-theme="light"] {
  --c-bg:       #FFFFFF;
  --c-surface:  rgba(0, 0, 0, 0.035);
  --c-surface2: rgba(0, 0, 0, 0.05);
  --c-border:   rgba(0, 0, 0, 0.09);
  --c-border2:  rgba(0, 0, 0, 0.05);
  --c-text:     #1D1D1F;
  --c-muted:    #6E6E73;
  --c-dim:      #8E8E93;
  --c-accent:   #1D1D1F;
  --c-blue:     #1D1D1F;                       /* the accent: black */
  --c-on-accent: #FFFFFF;
  --c-green:    #1F8A3E;                       /* the "accessible" system colours: readable on white */
  --c-red:      #D70015;
  --c-orange:   #48484A;
  --c-purple:   #8E8E93;
  --c-gold:     #636366;

  --card-bg:     #FFFFFF;
  --card-border: rgba(0, 0, 0, 0.07);
  --chrome-bg:   rgba(250, 250, 250, 0.88);
  --float-bg:    rgba(255, 255, 255, 0.98);
  --modal-overlay: rgba(0, 0, 0, 0.25);
  --shadow-card: 0 1px 2px rgba(0, 0, 0, 0.04), 0 8px 24px rgba(0, 0, 0, 0.05);
  --shadow-pop:  0 0 0 1px rgba(0, 0, 0, 0.05), 0 28px 72px rgba(0, 0, 0, 0.18);
  --focus-ring:  0 0 0 3px rgba(0, 0, 0, 0.12);
  --selection:   rgba(0, 0, 0, 0.14);
  --page-bg:     #F5F5F7;

  color-scheme: light;
}

/* ── Studio: type, surfaces, shape ──────────────────────────── */
html[data-style="studio"] body,
html[data-style="studio"] #root {
  font-family: 'Inter Variable', -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI Variable Text',
               'Segoe UI', system-ui, sans-serif;
  font-size: 14px;
  font-variant-numeric: tabular-nums;          /* numbers line up in every table */
  letter-spacing: -0.006em;
  -webkit-font-smoothing: antialiased;
  color: var(--c-text);
}
html[data-style="studio"] body { background: var(--page-bg); background-attachment: fixed; }

/* Cards (shared.tsx: data-card) are frosted glass with a soft shadow; a card inside a card is a quiet tile. */
html[data-style="studio"] [data-card] {
  background: var(--card-bg) !important;
  border-color: var(--card-border) !important;
  border-radius: 18px !important;
  -webkit-backdrop-filter: saturate(180%) blur(24px);
          backdrop-filter: saturate(180%) blur(24px);
  box-shadow: var(--shadow-card);
}
html[data-style="studio"] [data-card] [data-card] {
  background: var(--c-surface) !important;
  border-radius: 14px !important;
  -webkit-backdrop-filter: none;
          backdrop-filter: none;
  box-shadow: none;
}
/* The app's chrome (top bar, sidebar) and floating panels (menus, dialogs, the copilot drawer, chart tooltips). */
html[data-style="studio"] [data-glass] {
  -webkit-backdrop-filter: saturate(180%) blur(30px);
          backdrop-filter: saturate(180%) blur(30px);
}
html[data-style="studio"] [data-glass="chrome"] { background: var(--chrome-bg) !important; }
html[data-style="studio"] [data-glass="float"]  { background: var(--float-bg) !important; box-shadow: var(--shadow-pop) !important; }
/* Behind a dialog the page blurs, as in macOS. */
html[data-style="studio"] [style*="background: var(--modal-overlay)"] {
  -webkit-backdrop-filter: blur(8px) saturate(120%);
          backdrop-filter: blur(8px) saturate(120%);
}

/* Rounder, calmer shapes (components carry small inline radii). */
html[data-style="studio"] [style*="border-radius: 2px"] { border-radius: 999px !important; }
html[data-style="studio"] [style*="border-radius: 3px"] { border-radius: 8px   !important; }
html[data-style="studio"] [style*="border-radius: 4px"] { border-radius: 10px  !important; }
html[data-style="studio"] [style*="border-radius: 6px"] { border-radius: 14px  !important; }
html[data-style="studio"] [style*="border-radius: 8px"] { border-radius: 14px  !important; }

/* Sentence-case labels, no wide tracking: the shouting-uppercase look belongs to the terminal style. */
html[data-style="studio"] [style*="text-transform: uppercase"] {
  text-transform: none !important;
  font-weight: 600;
}
html[data-style="studio"] [style*="letter-spacing"] { letter-spacing: 0 !important; }

/* Headings (shared.tsx): a large page title, card titles in the text colour. */
html[data-style="studio"] [data-page-title] {
  font-size: 28px !important; font-weight: 700 !important; letter-spacing: -0.022em !important; line-height: 1.15;
}
html[data-style="studio"] [data-card-title] {
  font-size: 15px !important; font-weight: 600 !important; color: var(--c-text) !important;
  letter-spacing: -0.012em !important;
}

/* Controls */
html[data-style="studio"] input,
html[data-style="studio"] select,
html[data-style="studio"] textarea { transition: border-color .15s ease, box-shadow .15s ease; }
html[data-style="studio"] input:focus,
html[data-style="studio"] select:focus,
html[data-style="studio"] textarea:focus { outline: none; border-color: var(--c-blue) !important; box-shadow: var(--focus-ring); }
html[data-style="studio"] button {
  transition: transform .16s cubic-bezier(.2, .9, .3, 1.2), filter .12s ease, background-color .15s ease,
              border-color .15s ease, box-shadow .15s ease;
}
html[data-style="studio"] button:not(:disabled):hover  { filter: brightness(1.08); }
html[data-style="studio"] button:not(:disabled):active { transform: scale(0.97); transition-duration: 60ms; }
html[data-style="studio"] button:focus-visible,
html[data-style="studio"] a:focus-visible { outline: none; box-shadow: var(--focus-ring); }

/* Tables: hairlines and a quiet hover. */
html[data-style="studio"] table { border-collapse: collapse; }
html[data-style="studio"] th { font-weight: 500; }
html[data-style="studio"] tbody tr { transition: background-color .12s ease; }
html[data-style="studio"] tbody tr:hover { background-color: var(--c-surface2); }

html[data-style="studio"] ::selection { background: var(--selection); }
html[data-style="studio"] ::-webkit-scrollbar { width: 10px; height: 10px; }
html[data-style="studio"] ::-webkit-scrollbar-track { background: transparent; }
html[data-style="studio"] ::-webkit-scrollbar-thumb {
  background: var(--c-border); border-radius: 10px; border: 2px solid transparent; background-clip: content-box;
}
html[data-style="studio"] ::-webkit-scrollbar-thumb:hover { background: var(--c-dim); background-clip: content-box; }
`

/* Liquid Glass — Mahnoor & Maham (theme/liquid-glass, b4ebf21), as they designed it; only scoped to its style. */
const GLASS = `
html[data-style="glass"][data-theme="dark"] {
  --c-bg:       #0B0B0D;
  --c-surface:  rgba(44, 44, 48, 0.55);
  --c-surface2: rgba(255, 255, 255, 0.07);
  --c-border:   rgba(255, 255, 255, 0.14);
  --c-border2:  rgba(255, 255, 255, 0.07);
  --c-text:     #F5F5F7;
  --c-muted:    #AEAEB2;
  --c-dim:      #8A8A8E;
  --c-accent:   #F5F5F7;
  --c-blue:     #6E6E73;
  --c-green:    #78927F;
  --c-red:      #A97B76;
  --c-orange:   #A8956F;
  --c-purple:   #C7C7CC;
  --c-gold:     #E5E5EA;
  --c-on-accent: #FFFFFF;
  --modal-overlay: rgba(0, 0, 0, 0.55);
  --glass-blur:      saturate(180%) blur(28px);
  --glass-shadow:    0 8px 32px rgba(0, 0, 0, 0.45);
  --glass-edge:      inset 0 1px 0 rgba(255, 255, 255, 0.18), inset 0 0 0 1px rgba(255, 255, 255, 0.04);
  --page-bg:
    radial-gradient(900px 600px at 12% 8%,  rgba(255, 255, 255, 0.17), transparent 60%),
    radial-gradient(800px 700px at 92% 18%, rgba(170, 170, 185, 0.24), transparent 60%),
    radial-gradient(900px 800px at 55% 105%, rgba(255, 255, 255, 0.12), transparent 60%),
    linear-gradient(160deg, #1C1C1F 0%, #0B0B0D 55%, #151517 100%);
  color-scheme: dark;
}
html[data-style="glass"][data-theme="light"] {
  --c-bg:       #F2F2F4;
  --c-surface:  rgba(255, 255, 255, 0.62);
  --c-surface2: rgba(255, 255, 255, 0.55);
  --c-border:   rgba(60, 60, 67, 0.18);
  --c-border2:  rgba(60, 60, 67, 0.09);
  --c-text:     #1C1C1E;
  --c-muted:    #4A4A4F;
  --c-dim:      #7C7C82;
  --c-accent:   #1C1C1E;
  --c-blue:     #3A3A3C;
  --c-green:    #55705F;
  --c-red:      #8F5B57;
  --c-orange:   #86724A;
  --c-purple:   #636366;
  --c-gold:     #48484A;
  --c-on-accent: #FFFFFF;
  --modal-overlay: rgba(28, 28, 30, 0.35);
  --glass-blur:      saturate(180%) blur(28px);
  --glass-shadow:    0 8px 32px rgba(40, 40, 50, 0.14);
  --glass-edge:      inset 0 1px 0 rgba(255, 255, 255, 0.9), inset 0 0 0 1px rgba(255, 255, 255, 0.35);
  --page-bg:
    radial-gradient(900px 600px at 10% 5%,  rgba(255, 255, 255, 0.95), transparent 60%),
    radial-gradient(800px 700px at 95% 20%, rgba(190, 190, 200, 0.55), transparent 60%),
    radial-gradient(900px 800px at 50% 110%, rgba(150, 150, 160, 0.40), transparent 60%),
    linear-gradient(160deg, #E9E9EC 0%, #D6D6DA 55%, #E4E4E8 100%);
  color-scheme: light;
}
html[data-style="glass"] body,
html[data-style="glass"] #root {
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'SF Pro Display', 'Inter', 'Segoe UI Variable', 'Segoe UI',
               system-ui, sans-serif;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
  letter-spacing: -0.005em;
  color: var(--c-text);
}
html[data-style="glass"] body { background: var(--page-bg); background-attachment: fixed; }
html[data-style="glass"] [style*="background: var(--c-surface);"] {
  -webkit-backdrop-filter: var(--glass-blur);
          backdrop-filter: var(--glass-blur);
  box-shadow: var(--glass-shadow), var(--glass-edge);
}
html[data-style="glass"] input:focus,
html[data-style="glass"] select:focus,
html[data-style="glass"] textarea:focus { outline: none; box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.18); }
html[data-style="glass"][data-theme="light"] input:focus,
html[data-style="glass"][data-theme="light"] select:focus,
html[data-style="glass"][data-theme="light"] textarea:focus { box-shadow: 0 0 0 3px rgba(60, 60, 67, 0.18); }
html[data-style="glass"] button { transition: transform 160ms cubic-bezier(.2, .9, .3, 1.2), filter 120ms ease, background-color .15s ease; }
html[data-style="glass"] button:not(:disabled):hover  { filter: brightness(1.12); }
html[data-style="glass"] button:not(:disabled):active { transform: scale(0.97); transition-duration: 60ms; }
html[data-style="glass"] [style*="border-radius: 2px"] { border-radius: 99px !important; }
html[data-style="glass"] [style*="border-radius: 3px"] { border-radius: 8px  !important; }
html[data-style="glass"] [style*="border-radius: 4px"] { border-radius: 10px !important; }
html[data-style="glass"] [style*="border-radius: 6px"] { border-radius: 16px !important; }
html[data-style="glass"] table { border-collapse: collapse; }
html[data-style="glass"] ::-webkit-scrollbar { width: 10px; height: 10px; }
html[data-style="glass"] ::-webkit-scrollbar-track { background: transparent; }
html[data-style="glass"] ::-webkit-scrollbar-thumb {
  background: var(--c-border); border-radius: 10px; border: 2px solid transparent; background-clip: content-box;
}
`

/* Terminal — the original ApnaSOR look. */
const TERMINAL = `
html[data-style="terminal"][data-theme="dark"] {
  --c-bg:       #0D0F11;
  --c-surface:  #131619;
  --c-surface2: #1A1E24;
  --c-border:   #2A2E35;
  --c-border2:  #1A1E24;
  --c-text:     #E8E6DF;
  --c-muted:    #9C9A92;
  --c-dim:      #5F5E5A;
  --c-accent:   #00D9FF;
  --c-blue:     #3B8BD4;
  --c-green:    #4CAF50;
  --c-red:      #E24B4A;
  --c-orange:   #EF9F27;
  --c-purple:   #7F77DD;
  --c-gold:     #FFC72C;
  --c-on-accent: #FFFFFF;
  --modal-overlay: rgba(0, 0, 0, 0.7);
  color-scheme: dark;
}
html[data-style="terminal"][data-theme="light"] {
  --c-bg:       #F4F6F9;
  --c-surface:  #FFFFFF;
  --c-surface2: #EDF0F4;
  --c-border:   #D2D7DE;
  --c-border2:  #E4E8ED;
  --c-text:     #131923;
  --c-muted:    #4A5363;
  --c-dim:      #7A8294;
  --c-accent:   #006BA1;
  --c-blue:     #1F6FE5;
  --c-green:    #137A3A;
  --c-red:      #C8302F;
  --c-orange:   #C26800;
  --c-purple:   #5C4FCC;
  --c-gold:     #B8860B;
  --c-on-accent: #FFFFFF;
  --modal-overlay: rgba(20, 30, 45, 0.45);
  color-scheme: light;
}
html[data-style="terminal"] body,
html[data-style="terminal"] #root {
  background: var(--c-bg);
  color: var(--c-text);
  font-family: 'Consolas', 'IBM Plex Mono', monospace;
  font-size: 14px;
}
/* Its signature: small letter-spaced labels in capitals (the labels themselves are written in sentence case). */
html[data-style="terminal"] [style*="letter-spacing"] { text-transform: uppercase; }
html[data-style="terminal"] ::-webkit-scrollbar { width: 8px; height: 8px; }
html[data-style="terminal"] ::-webkit-scrollbar-track { background: var(--c-bg); }
html[data-style="terminal"] ::-webkit-scrollbar-thumb { background: var(--c-border); border-radius: 4px; }
`

/* Shared by every look */
const COMMON = `
html, body, #root { transition: background-color .15s ease, color .15s ease; }
input, select, textarea, button { font-family: inherit; color: var(--c-text); }
input::placeholder, textarea::placeholder { color: var(--c-dim); }
/* Thinking orbs (components/Orb.tsx): fade in; the copilot's thinking line shimmers. */
@keyframes orbIn      { from { opacity: 0; transform: scale(.85); } to { opacity: 1; transform: none; } }
@keyframes orbShimmer { from { background-position: 100% 0; } to { background-position: -100% 0; } }
@keyframes riseIn     { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

/* A field drawn as a box around an icon and an input (data-field): the box shows the focus. */
[data-field]:focus-within { border-color: var(--c-blue) !important; }
html[data-style="studio"] [data-field]:focus-within { box-shadow: var(--focus-ring); }
html[data-style="studio"] [data-field] input:focus { box-shadow: none; }
select option { background: var(--c-bg); color: var(--c-text); }

/* Accessibility: respect the operating system's settings */
@media (prefers-reduced-transparency: reduce) {
  html[data-style="glass"][data-theme="dark"]  { --c-surface: #1C1C1F; --c-surface2: #2A2A2E; }
  html[data-style="glass"][data-theme="light"] { --c-surface: #FFFFFF; --c-surface2: #F2F2F4; }
  html[data-style="studio"][data-theme="dark"]  { --card-bg: #1C1C1F; --chrome-bg: #141417; --float-bg: #222226; }
  html[data-style="studio"][data-theme="light"] { --card-bg: #FFFFFF; --chrome-bg: #FAFAFA; --float-bg: #FFFFFF; }
  [style*="background: var(--c-surface);"], [data-card], [data-glass] {
    -webkit-backdrop-filter: none !important; backdrop-filter: none !important;
  }
}
@media (prefers-reduced-motion: reduce) {
  button { transition: none !important; }
  [role="status"] *, [data-rise] { animation: none !important; }
}
`

export const THEME_CSS = STUDIO + GLASS + TERMINAL + COMMON

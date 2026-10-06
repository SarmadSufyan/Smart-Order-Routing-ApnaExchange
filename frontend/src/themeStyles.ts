/**
 * Global theme CSS — injected once into <head>.
 *
 * Grey / white / black "liquid glass" theme (iOS-style frosted panels).
 *
 * How it works:
 *  - Every colour in the app is a `--c-*` variable (see theme.ts), so the
 *    palette below restyles all pages at once.
 *  - Surfaces are translucent; `.glass` / `.glass-thin` add the blur.
 *  - Components keep their inline styles. A few attribute-selector rules at the
 *    bottom round the corners everywhere, so we don't touch every page.
 *
 * Swapping `data-theme` on <html> picks dark or light. Both stay grey/white/black;
 * only green / red / amber are kept as colour, because P&L and risk states need them.
 */

export const THEME_CSS = `
:root, html[data-theme="dark"] {
  --c-bg:       #0B0B0D;                    /* opaque: used for inset fields + text-on-light */
  --c-surface:  rgba(44, 44, 48, 0.55);     /* glass panel */
  --c-surface2: rgba(255, 255, 255, 0.07);  /* nested card / chip on glass */
  --c-border:   rgba(255, 255, 255, 0.14);
  --c-border2:  rgba(255, 255, 255, 0.07);
  --c-text:     #F5F5F7;
  --c-muted:    #AEAEB2;
  --c-dim:      #8A8A8E;
  --c-accent:   #F5F5F7;                    /* monochrome accent */
  --c-blue:     #6E6E73;                    /* primary action = graphite (white text stays readable) */
  --c-green:    #78927F;
  --c-red:      #A97B76;
  --c-orange:   #A8956F;
  --c-purple:   #C7C7CC;
  --c-gold:     #E5E5EA;

  --modal-overlay: rgba(0, 0, 0, 0.55);

  --glass-blur:      saturate(180%) blur(28px);
  --glass-blur-thin: saturate(160%) blur(16px);
  --glass-shadow:    0 8px 32px rgba(0, 0, 0, 0.45);
  --glass-edge:      inset 0 1px 0 rgba(255, 255, 255, 0.18), inset 0 0 0 1px rgba(255, 255, 255, 0.04);

  --page-bg:
    radial-gradient(900px 600px at 12% 8%,  rgba(255, 255, 255, 0.17), transparent 60%),
    radial-gradient(800px 700px at 92% 18%, rgba(170, 170, 185, 0.24), transparent 60%),
    radial-gradient(900px 800px at 55% 105%, rgba(255, 255, 255, 0.12), transparent 60%),
    linear-gradient(160deg, #1C1C1F 0%, #0B0B0D 55%, #151517 100%);

  color-scheme: dark;
}

html[data-theme="light"] {
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

  --modal-overlay: rgba(28, 28, 30, 0.35);

  --glass-blur:      saturate(180%) blur(28px);
  --glass-blur-thin: saturate(160%) blur(16px);
  --glass-shadow:    0 8px 32px rgba(40, 40, 50, 0.14);
  --glass-edge:      inset 0 1px 0 rgba(255, 255, 255, 0.9), inset 0 0 0 1px rgba(255, 255, 255, 0.35);

  --page-bg:
    radial-gradient(900px 600px at 10% 5%,  rgba(255, 255, 255, 0.95), transparent 60%),
    radial-gradient(800px 700px at 95% 20%, rgba(190, 190, 200, 0.55), transparent 60%),
    radial-gradient(900px 800px at 50% 110%, rgba(150, 150, 160, 0.40), transparent 60%),
    linear-gradient(160deg, #E9E9EC 0%, #D6D6DA 55%, #E4E4E8 100%);

  color-scheme: light;
}

html {
  background: var(--page-bg);
  background-attachment: fixed;
}
html, body, #root {
  background: transparent;
  color: var(--c-text);
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'SF Pro Display', 'Inter', 'Segoe UI Variable', 'Segoe UI', system-ui, sans-serif;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
  letter-spacing: -0.005em;
}
html[data-theme] body { background: var(--page-bg); background-attachment: fixed; }

/* Glass surfaces */
.glass {
  -webkit-backdrop-filter: var(--glass-blur);
          backdrop-filter: var(--glass-blur);
  box-shadow: var(--glass-shadow), var(--glass-edge);
}
.glass-thin {
  -webkit-backdrop-filter: var(--glass-blur-thin);
          backdrop-filter: var(--glass-blur-thin);
  box-shadow: var(--glass-edge);
}

/* Form controls */
input, select, textarea, button {
  font-family: inherit;
  color: var(--c-text);
}
input::placeholder, textarea::placeholder { color: var(--c-dim); }
input:focus, select:focus, textarea:focus { outline: none; box-shadow: 0 0 0 3px rgba(255,255,255,0.18); }
html[data-theme="light"] input:focus,
html[data-theme="light"] select:focus,
html[data-theme="light"] textarea:focus { box-shadow: 0 0 0 3px rgba(60,60,67,0.18); }

select option { background: var(--c-bg); color: var(--c-text); }

/* Press feedback: instant on pointer-down, springy on release */
button { transition: transform 160ms cubic-bezier(.2,.9,.3,1.2), filter 120ms ease, background-color .15s ease; }
button:not(:disabled):hover  { filter: brightness(1.12); }
button:not(:disabled):active { transform: scale(0.97); transition-duration: 60ms; }

/* Rounder corners everywhere (components use small inline radii) */
[style*="border-radius: 2px"] { border-radius: 99px !important; }
[style*="border-radius: 3px"] { border-radius: 8px  !important; }
[style*="border-radius: 4px"] { border-radius: 10px !important; }
[style*="border-radius: 6px"] { border-radius: 16px !important; }

/* Tables: hairlines, no heavy rules */
table { border-collapse: collapse; }

/* Scrollbars */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: var(--c-border);
  border-radius: 10px;
  border: 2px solid transparent;
  background-clip: content-box;
}
::-webkit-scrollbar-thumb:hover { background: var(--c-dim); background-clip: content-box; }

/* Accessibility: respect the OS settings */
@media (prefers-reduced-transparency: reduce) {
  :root, html[data-theme="dark"]  { --c-surface: #26262A; --c-surface2: #303034; }
  html[data-theme="light"]        { --c-surface: #FFFFFF; --c-surface2: #F2F2F4; }
  .glass, .glass-thin { -webkit-backdrop-filter: none; backdrop-filter: none; }
}
@media (prefers-reduced-motion: reduce) {
  button { transition: none; }
}
`

/**
 * Global theme CSS — injected once into <head>.
 *
 * Defines the same set of `--c-*` variables for both modes. Swapping the
 * `data-theme` attribute on <html> picks the right palette.
 */

export const THEME_CSS = `
:root, html[data-theme="dark"] {
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

  --modal-overlay: rgba(0, 0, 0, 0.7);

  color-scheme: dark;
}

html[data-theme="light"] {
  --c-bg:       #F4F6F9;        /* page background — soft, not pure white */
  --c-surface:  #FFFFFF;        /* cards */
  --c-surface2: #EDF0F4;        /* nested panel */
  --c-border:   #D2D7DE;        /* solid border */
  --c-border2:  #E4E8ED;        /* subtle separator */
  --c-text:     #131923;        /* near-black for contrast */
  --c-muted:    #4A5363;        /* secondary text */
  --c-dim:      #7A8294;        /* tertiary / hints */
  --c-accent:   #006BA1;        /* darker cyan-blue: visible on white */
  --c-blue:     #1F6FE5;        /* primary action */
  --c-green:    #137A3A;        /* gains — darker than 4CAF50 for contrast */
  --c-red:      #C8302F;        /* losses */
  --c-orange:   #C26800;        /* warnings */
  --c-purple:   #5C4FCC;
  --c-gold:     #B8860B;

  --modal-overlay: rgba(20, 30, 45, 0.45);

  color-scheme: light;
}

html, body, #root {
  background: var(--c-bg);
  color: var(--c-text);
  font-family: 'Consolas', 'IBM Plex Mono', monospace;
  font-size: 14px;
  transition: background-color .15s ease, color .15s ease;
}

/* Sane defaults for form inputs in both themes */
input, select, textarea, button {
  font-family: inherit;
  color: var(--c-text);
}
input::placeholder, textarea::placeholder { color: var(--c-dim); }

/* Native dropdown options should match the theme background */
select option {
  background: var(--c-surface);
  color: var(--c-text);
}

/* Scrollbars — keep them subtle in both modes */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--c-bg); }
::-webkit-scrollbar-thumb {
  background: var(--c-border);
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover { background: var(--c-dim); }
`

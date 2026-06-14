import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { THEME_CSS } from './themeStyles'

// Inject theme CSS once, before any component paints. This sets the
// :root / [data-theme] variables that every C.* token resolves against.
const styleEl = document.createElement('style')
styleEl.setAttribute('data-poc-theme', '')
styleEl.appendChild(document.createTextNode(THEME_CSS))
document.head.appendChild(styleEl)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)

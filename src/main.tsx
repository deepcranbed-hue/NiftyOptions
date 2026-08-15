import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import { BrowserRouter, useLocation } from 'react-router-dom';
import App from './App.tsx';
import { Nifty50StockPage } from './components/Nifty50StockPage.tsx';
import { AIInfraCompanyPage } from './components/AIInfraCompanyPage.tsx';
import './index.css';

/**
 * Root — App, except on the standalone per-stock route.
 *
 * /intel/nifty50/<SYMBOL> is opened in a new tab from the Nifty 50 table and renders
 * WITHOUT the app chrome: no sidebar, no header, the full window for one company. That
 * is the entire point of the route — the inline expand inside a table cell is the
 * cramped version, this is the readable one.
 *
 * Deliberately a pathname test rather than a <Routes> wrapper around <App />: App
 * mounts every panel at once and hides the inactive ones by comparing
 * location.pathname itself, and it has its own nested <Routes> for the "/" redirect.
 * Nesting it under a catch-all route would change what those descendant routes see.
 * This keeps the existing navigation untouched.
 */
function Root() {
  const { pathname } = useLocation();
  const m = pathname.match(/^\/intel\/nifty50\/([^/]+)\/?$/);
  if (m) return <Nifty50StockPage symbol={decodeURIComponent(m[1])} />;
  // Same pattern, same reason: one AI-infra company gets the whole window rather
  // than a row that expands inside a 12-column table.
  const ai = pathname.match(/^\/intel\/ai-infra\/([^/]+)\/?$/);
  if (ai) return <AIInfraCompanyPage symbol={decodeURIComponent(ai[1])} />;
  return <App />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Root />
    </BrowserRouter>
  </StrictMode>,
);

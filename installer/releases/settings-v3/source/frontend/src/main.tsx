import { StrictMode } from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import { initializeManagementSession } from './updates/management';

// Consume and scrub the local-only updater capability before rendering or links.
initializeManagementSession();

// Older builds (Create React App) registered a service worker; remove it from returning browsers
if ('serviceWorker' in navigator) {
  navigator.serviceWorker
    .getRegistrations()
    .then((registrations) => registrations.forEach((r) => void r.unregister()))
    .catch(() => {});
}

const container = document.getElementById('root');
if (!container) throw new Error('Missing #root element');
ReactDOM.createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

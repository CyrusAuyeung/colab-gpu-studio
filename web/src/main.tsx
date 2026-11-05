import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';
import './styles/offline.css';

document.documentElement.dataset.theme = localStorage.getItem('studio-theme') || 'system';

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);

import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import { createLiveSource } from './data/live.js';
import { createReplaySource } from './data/replay.js';

// A page built with a recorded run inside (node build.mjs --replay …) plays it back; otherwise it talks to the
// Python backend that serves it.
const src = typeof window !== 'undefined' && window.__PADDY_REPLAY__ ? createReplaySource(window.__PADDY_REPLAY__) : createLiveSource();
// for the browser tests (and anyone curious in the console)
if (typeof window !== 'undefined') window.__PADDY__ = { src };
createRoot(document.getElementById('root')).render(<App src={src} />);

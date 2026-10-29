// Offline build with esbuild (no Vite, no network needed).
//
//   node build.mjs                          → dist/ (index.html, assets/app.js, assets/app.css, fonts/), served by
//                                             `python -m paddy serve` at /
//   node build.mjs --replay runs/x/replay.json [--out dist-replay/paddy-replay.html] [--artifact]
//                                           → one self-contained HTML file that replays a recorded run without a
//                                             server (fonts, styles, script and data inlined). --artifact leaves out
//                                             the <!doctype>, <html>, <head> and <body> tags, for hosts that wrap the
//                                             page in their own document (such as a claude.ai artifact).
//
// Packages are taken from ./node_modules when `npm install` has been run, otherwise from the folder in
// NODE_TOOLS (default /opt/npm-tools/node_modules), where this copy was built from.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const tools = process.env.NODE_TOOLS || '/opt/npm-tools/node_modules';
const nodePaths = [path.join(here, 'node_modules'), tools].filter((p) => fs.existsSync(p));
const require = createRequire(import.meta.url);

function load(name) {
  for (const base of nodePaths) {
    try {
      return require(require.resolve(name, { paths: [base] }));
    } catch (e) { /* next */ }
  }
  throw new Error(`cannot find ${name}: run npm install, or set NODE_TOOLS to a folder with it`);
}
const esbuild = load('esbuild');

const args = process.argv.slice(2);
const arg = (k, d = null) => { const i = args.indexOf(k); return i >= 0 && args[i + 1] ? args[i + 1] : d; };
const replay = arg('--replay');

async function bundle() {
  const r = await esbuild.build({
    entryPoints: [path.join(here, 'src/main.jsx')],
    bundle: true,
    write: false,
    format: 'iife',
    target: ['es2020'],
    jsx: 'automatic',
    minify: true,
    legalComments: 'none',
    nodePaths,
    define: { 'process.env.NODE_ENV': '"production"' },
    logLevel: 'warning',
  });
  return r.outputFiles[0].text;
}

const css = fs.readFileSync(path.join(here, 'src/styles.css'), 'utf8');
const fontsDir = path.join(here, 'public/fonts');

if (!replay) {
  const out = path.join(here, 'dist');
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(path.join(out, 'assets'), { recursive: true });
  fs.mkdirSync(path.join(out, 'fonts'), { recursive: true });
  const js = await bundle();
  fs.writeFileSync(path.join(out, 'assets/app.js'), js);
  fs.writeFileSync(path.join(out, 'assets/app.css'), css.replaceAll('url(/fonts/', 'url(../fonts/'));
  for (const f of fs.readdirSync(fontsDir)) fs.copyFileSync(path.join(fontsDir, f), path.join(out, 'fonts', f));
  const html = fs.readFileSync(path.join(here, 'index.html'), 'utf8')
    .replace('<script type="module" src="./src/main.jsx"></script>', '<script src="./assets/app.js" defer></script>')
    .replace('</head>', '    <link rel="stylesheet" href="./assets/app.css" />\n  </head>');
  fs.writeFileSync(path.join(out, 'index.html'), html);
  const kb = (p) => (fs.statSync(p).size / 1024).toFixed(0);
  console.log(`dist/ written: app.js ${kb(path.join(out, 'assets/app.js'))} KB, app.css ${kb(path.join(out, 'assets/app.css'))} KB`);
} else {
  const data = fs.readFileSync(path.resolve(replay), 'utf8');
  JSON.parse(data); // fail early on a bad file
  const js = await bundle();
  let inlineCss = css;
  for (const f of fs.readdirSync(fontsDir).filter((x) => x.endsWith('.woff'))) {
    const b64 = fs.readFileSync(path.join(fontsDir, f)).toString('base64');
    inlineCss = inlineCss.replaceAll(`url(/fonts/${f})`, `url(data:font/woff;base64,${b64})`);
  }
  const safe = (s) => s.replace(/<\/(script)/gi, '<\\/$1').replace(/<!--/g, '<\\!--');
  const outFile = path.resolve(arg('--out', path.join(here, 'dist-replay/paddy-replay.html')));
  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  const meta = JSON.parse(data);
  const title = meta.stand_in ? 'Paddy API Farm replay (stand-in)' : 'Paddy API Farm replay';
  const artifact = args.includes('--artifact');
  const html = artifact ? `<title>Paddy API Farm Replay</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="description" content="${meta.stand_in ? 'Stand-in replay: decisions from the mock decision API (the built-in manager\'s choices), not the real API.' : 'A recorded season.'}">
<style>${inlineCss}</style>
<div id="root"></div>
<script>window.__PADDY_REPLAY__ = ${safe(data)};</script>
<script>${safe(js)}</script>
` : `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>${title}</title>
<style>${inlineCss}</style>
</head>
<body>
<div id="root"></div>
<script>window.__PADDY_REPLAY__ = ${safe(data)};</script>
<script>${safe(js)}</script>
</body>
</html>`;
  fs.writeFileSync(outFile, html);
  console.log(`${path.relative(process.cwd(), outFile)} written (${(html.length / 1024 / 1024).toFixed(2)} MB)`);
}

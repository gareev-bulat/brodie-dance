// Renders web/assets/models/verity.glb (ChocoChip67, CC BY 4.0) into the raw frames verity/models.py turns into each
// character's call video. Headless Chromium + three.js, so no GPU or Blender needed. Then: python -m verity.models frames
//   node scripts/render_model.js            (needs: npm i playwright; three.js comes from jsdelivr,
//   THREE_DIR=node_modules/three node ...    or from a local copy when the CDN is blocked)
// Output: data/tmp/model_frames/<face>_NN.png, 360x640 RGBA, the ball centred with radius 151 px (verity/avatar.py CX, CY, R).
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const ROOT = path.join(__dirname, '..'), OUT = path.join(ROOT, 'data', 'tmp', 'model_frames');
const FACES = ['mouth_closed_idle', 'mouth_open_idle', 'mouth_closed_sus', 'mouth_open_sus', 'mouth_closed_bored'];
const N = 24;   // one slow turn-and-bob cycle (verity/models.py IDLE_FRAMES)

const PAGE = `<!doctype html><meta charset="utf-8"><body style="margin:0">
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
 "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
<script type="module">
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
const W = 360, H = 640, R = 151;
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
renderer.setSize(W, H); renderer.setClearColor(0, 0); renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.append(renderer.domElement);
const scene = new THREE.Scene();
scene.add(new THREE.AmbientLight(0xffffff, 1.6));
const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(-2, 3, 4); scene.add(key);   // top left, like the drawn ball
const model = (await new GLTFLoader().loadAsync('/web/assets/models/verity.glb')).scene;
model.traverse((o) => {   // some spheres ship faceted: weld the seams and smooth the normals
  if (o.isMesh) { const g = mergeVertices(o.geometry.deleteAttribute('normal'), 1e-4); g.computeVertexNormals(); o.geometry = g; }
});
const faces = {};
model.traverse((o) => { if (/^mouth_/.test(o.name)) faces[o.name.replace(/_\\d+$/, '')] = o; });
const pivot = new THREE.Group(); pivot.add(model); scene.add(pivot);
model.updateMatrixWorld(true);
const box = new THREE.Box3().setFromObject(faces.mouth_closed_idle), r = box.getSize(new THREE.Vector3()).y / 2;
model.position.sub(box.getCenter(new THREE.Vector3()));   // ball centre at the origin
const half = r * (H / 2) / R;
const cam = new THREE.OrthographicCamera(-half * W / H, half * W / H, half, -half, -100, 100); cam.position.z = 10;
window.shot = (face, yaw, bob) => {   // the model faces -z, hence the 180
  for (const [n, o] of Object.entries(faces)) o.visible = n === face;
  pivot.rotation.set(0, THREE.MathUtils.degToRad(180 + yaw), 0); pivot.position.y = bob * r;
  renderer.render(scene, cam);
  return renderer.domElement.toDataURL('image/png');
};
window.ready = true;
</script>`;

(async () => {
  const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.error(e.message));
  await page.route('http://render.local/**', (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/') return route.fulfill({ contentType: 'text/html', body: PAGE });
    route.fulfill({ path: path.join(ROOT, p) });
  });
  if (process.env.THREE_DIR) await page.route('https://cdn.jsdelivr.net/npm/three@0.160.0/**', (route) =>
    route.fulfill({ path: path.join(process.env.THREE_DIR, new URL(route.request().url()).pathname.replace('/npm/three@0.160.0/', '')) }));
  await page.goto('http://render.local/');
  await page.waitForFunction(() => window.ready, null, { timeout: 60000 });
  fs.mkdirSync(OUT, { recursive: true });
  for (const face of FACES) for (let i = 0; i < N; i++) {
    const t = i / N, yaw = 14 * Math.sin(2 * Math.PI * t), bob = 0.04 * Math.sin(4 * Math.PI * t);
    const url = await page.evaluate(([f, y, b]) => window.shot(f, y, b), [face, yaw, bob]);
    fs.writeFileSync(path.join(OUT, `${face}_${String(i).padStart(2, '0')}.png`), Buffer.from(url.split(',')[1], 'base64'));
  }
  console.log(`rendered ${FACES.length} faces x ${N} frames -> ${path.relative(ROOT, OUT)}`);
  await browser.close();
})();

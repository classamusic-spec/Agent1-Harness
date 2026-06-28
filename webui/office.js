// The "Office" — a tiny 3D agent that works while your build runs.
// Vendored three.js (offline-safe). Exposed as window.OfficeView so the classic
// app.js can drive it from /api/current.
import * as THREE from "./vendor/three.module.js";

const PALETTE = {
  idle: 0x35506b,
  building: 0x2ee6ff,
  passed: 0x41d6a3,
  failed: 0xff5d73,
};

let R = null; // runtime state (created on init)

function makeAgent() {
  const g = new THREE.Group();
  const skin = new THREE.MeshStandardMaterial({ color: 0x9fd0ff, roughness: 0.5, metalness: 0.2 });
  const suit = new THREE.MeshStandardMaterial({ color: 0x1b2740, roughness: 0.6, metalness: 0.3,
    emissive: 0x0a1830, emissiveIntensity: 0.4 });

  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.32, 0.5, 6, 12), suit);
  body.position.y = 0.95;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.26, 20, 20), skin);
  head.position.y = 1.5;
  // visor
  const visor = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.1, 0.04),
    new THREE.MeshStandardMaterial({ color: 0x07101c, emissive: 0x2ee6ff, emissiveIntensity: 1.2 }));
  visor.position.set(0, 1.52, 0.24);

  const armGeo = new THREE.CapsuleGeometry(0.08, 0.34, 4, 8);
  const lArm = new THREE.Mesh(armGeo, suit);
  const rArm = new THREE.Mesh(armGeo, suit);
  lArm.position.set(-0.34, 1.0, 0.18); lArm.rotation.z = 0.5; lArm.rotation.x = -0.9;
  rArm.position.set(0.34, 1.0, 0.18); rArm.rotation.z = -0.5; rArm.rotation.x = -0.9;

  g.add(body, head, visor, lArm, rArm);
  g.userData = { head, lArm, rArm };
  return g;
}

function makeMonitor() {
  const g = new THREE.Group();
  const frame = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.95, 0.06),
    new THREE.MeshStandardMaterial({ color: 0x0a121f, roughness: 0.4, metalness: 0.6 }));
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.38, 0.82),
    new THREE.MeshBasicMaterial({ color: PALETTE.idle }));
  screen.position.z = 0.04;
  const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.08, 0.4, 12),
    new THREE.MeshStandardMaterial({ color: 0x10202f, metalness: 0.7, roughness: 0.3 }));
  stand.position.y = -0.65;
  g.add(frame, screen, stand);
  g.userData = { screen };
  g.position.set(0, 1.5, -0.55);
  return g;
}

function init(canvas) {
  if (R || !canvas) return;
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true,
      preserveDrawingBuffer: true });
  } catch (e) {
    const s = document.getElementById("office-status");
    if (s) s.textContent = "3D unavailable (no WebGL)";
    return;
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x05080f, 0.055);

  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
  camera.position.set(5.5, 4.6, 7.5);
  camera.lookAt(0, 1, 0);

  const root = new THREE.Group();
  scene.add(root);

  // lights
  scene.add(new THREE.AmbientLight(0x33507a, 0.7));
  const dir = new THREE.DirectionalLight(0xbfe6ff, 0.8);
  dir.position.set(4, 8, 5);
  scene.add(dir);
  const accent = new THREE.PointLight(0x2ee6ff, 1.4, 18);
  accent.position.set(0, 2.4, -0.4);
  scene.add(accent);

  // floor + neon grid
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40),
    new THREE.MeshStandardMaterial({ color: 0x070c15, roughness: 0.9, metalness: 0.1 }));
  floor.rotation.x = -Math.PI / 2;
  root.add(floor);
  const grid = new THREE.GridHelper(40, 40, 0x2ee6ff, 0x12304a);
  grid.material.transparent = true; grid.material.opacity = 0.35;
  root.add(grid);

  // back wall neon strips
  for (let i = 0; i < 3; i++) {
    const strip = new THREE.Mesh(new THREE.BoxGeometry(6, 0.05, 0.05),
      new THREE.MeshStandardMaterial({ color: 0x07101c, emissive: 0x2050ff, emissiveIntensity: 1 }));
    strip.position.set(0, 1 + i * 1.1, -3);
    root.add(strip);
  }

  // desk
  const desk = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.18, 1.5),
    new THREE.MeshStandardMaterial({ color: 0x111c2b, roughness: 0.5, metalness: 0.5 }));
  desk.position.set(0, 0.85, 0);
  root.add(desk);

  const monitor = makeMonitor();
  root.add(monitor);

  const agent = makeAgent();
  agent.position.set(0, 0, 1.15);
  agent.rotation.y = Math.PI; // face the monitor
  root.add(agent);

  // build ring above the desk
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.5, 0.045, 12, 48),
    new THREE.MeshStandardMaterial({ color: 0x07101c, emissive: PALETTE.idle, emissiveIntensity: 1.2 }));
  ring.position.set(0, 2.7, 0);
  ring.rotation.x = Math.PI / 2;
  root.add(ring);

  // particles
  const pcount = 260;
  const ppos = new Float32Array(pcount * 3);
  for (let i = 0; i < pcount; i++) {
    ppos[i * 3] = (Math.random() - 0.5) * 16;
    ppos[i * 3 + 1] = Math.random() * 7;
    ppos[i * 3 + 2] = (Math.random() - 0.5) * 16;
  }
  const pgeo = new THREE.BufferGeometry();
  pgeo.setAttribute("position", new THREE.BufferAttribute(ppos, 3));
  const particles = new THREE.Points(pgeo,
    new THREE.PointsMaterial({ color: 0x2ee6ff, size: 0.04, transparent: true, opacity: 0.6 }));
  root.add(particles);

  const clock = new THREE.Clock();
  R = {
    renderer, scene, camera, root, monitor, agent, ring, particles, accent, clock,
    mode: "idle", targetColor: new THREE.Color(PALETTE.idle),
    autoRot: 0.12, dragRot: 0, dragging: false, lastX: 0, raf: 0,
  };

  // drag to orbit
  canvas.style.touchAction = "none";
  canvas.addEventListener("pointerdown", (e) => { R.dragging = true; R.lastX = e.clientX; });
  window.addEventListener("pointerup", () => { R.dragging = false; });
  window.addEventListener("pointermove", (e) => {
    if (!R.dragging) return;
    R.dragRot += (e.clientX - R.lastX) * 0.005; R.lastX = e.clientX;
  });

  resize();
  window.addEventListener("resize", resize);
  R.renderer.render(R.scene, R.camera); // one synchronous frame before the rAF loop
  animate();
}

function resize() {
  if (!R) return;
  const c = R.renderer.domElement;
  const w = c.clientWidth || c.parentElement.clientWidth || 640;
  const h = c.clientHeight || 420;
  R.renderer.setSize(w, h, false);
  R.camera.aspect = w / h;
  R.camera.updateProjectionMatrix();
}

function animate() {
  if (!R) return;
  R.raf = requestAnimationFrame(animate);
  const t = R.clock.getElapsedTime();
  const building = R.mode === "building";
  const speed = building ? 9 : 1.2;
  const amp = building ? 0.5 : 0.06;

  // typing arms
  const a = R.agent.userData;
  a.lArm.rotation.x = -0.9 + Math.sin(t * speed) * amp;
  a.rArm.rotation.x = -0.9 + Math.sin(t * speed + 1.1) * amp;
  a.head.position.y = 1.5 + Math.sin(t * 1.6) * 0.015;
  R.agent.position.y = Math.sin(t * 1.2) * 0.02;

  // ring + particles
  R.ring.rotation.z += building ? 0.08 : 0.01;
  R.particles.rotation.y += building ? 0.0016 : 0.0004;

  // color lerp on screen + ring + accent
  const screenMat = R.monitor.userData.screen.material;
  screenMat.color.lerp(R.targetColor, 0.08);
  R.ring.material.emissive.lerp(R.targetColor, 0.08);
  R.accent.color.lerp(R.targetColor, 0.08);
  // screen pulse while building
  screenMat.color.offsetHSL(0, 0, building ? Math.sin(t * 6) * 0.03 : 0);

  // camera orbit
  if (!R.dragging) R.dragRot += R.autoRot * 0.016;
  R.root.rotation.y = R.dragRot;

  R.renderer.render(R.scene, R.camera);
}

function setState(c) {
  if (!R) return;
  let mode = "idle";
  if (c.busy) mode = "building";
  else if (c.status === "passed") mode = "passed";
  else if (c.status === "failed" || c.status === "error") mode = "failed";
  R.mode = mode;
  R.autoRot = mode === "building" ? 0.05 : 0.14;
  R.targetColor.set(PALETTE[mode]);

  const statusEl = document.getElementById("office-status");
  const subEl = document.getElementById("office-sub");
  if (statusEl) {
    const labels = { idle: "Idle — awaiting work", building: "Building…", passed: "Build passed",
      failed: "Build failed" };
    statusEl.textContent = labels[mode];
    statusEl.dataset.mode = mode;
  }
  if (subEl) {
    const bits = [];
    if (c.name) bits.push(c.name);
    if (c.tokens) bits.push(`${c.tokens} tokens`);
    if (c.elapsed) bits.push(`${c.elapsed}s`);
    subEl.textContent = bits.join("  ·  ");
  }
}

window.OfficeView = { init, setState, resize };

// app.js (a classic script) runs applyHash() before this module executes, so if
// the Office tab is already the active panel on load, init here.
(function autostart() {
  const panel = document.getElementById("panel-office");
  if (panel && !panel.hidden) {
    init(document.getElementById("office-canvas"));
    setTimeout(resize, 50);
  }
})();

// The "Office" — a living 3D agent workspace that animates while your build runs.
// Vendored three.js (offline-safe). Exposed as window.OfficeView so the classic
// app.js can drive it from /api/current. A complete overhaul: multiple
// workstations with code scrolling across the monitors, a wall of GPU rigs whose
// fans spin and cards glow under load, plants, a coffee mug or two, warm rim
// lighting, and a crew of agents typing away.
import * as THREE from "./vendor/three.module.js";

const PALETTE = {
  idle: 0x35506b,
  building: 0x2ee6ff,
  passed: 0x41d6a3,
  failed: 0xff5d73,
};
const TINT_HEX = {
  idle: "#35506b",
  building: "#2ee6ff",
  passed: "#41d6a3",
  failed: "#ff5d73",
};

let R = null; // runtime state (created on init)

const mat = (o) => new THREE.MeshStandardMaterial(o);

// ── code screen ───────────────────────────────────────────────────────────
// A CanvasTexture that renders fake source code scrolling upward. Speed + tint
// react to the build mode, so the monitors look alive ("code going off").
function makeCodeScreen(w = 320, h = 200) {
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d");
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const COLORS = ["#7fd0ff", "#5fe3b0", "#ffd479", "#ff8cc6", "#9aa7b8", "#c9a7ff", "#6ad0ff"];
  const lineH = 15;
  const rows = Math.ceil(h / lineH) + 2;

  function newLine() {
    const indent = ((Math.random() * 5) | 0) * 12 + 26;
    const segs = [];
    let x = indent;
    const n = 1 + ((Math.random() * 4) | 0);
    for (let i = 0; i < n; i++) {
      const wseg = 16 + Math.random() * 72;
      if (x + wseg > w - 14) break;
      segs.push({ x, w: wseg, c: COLORS[(Math.random() * COLORS.length) | 0] });
      x += wseg + 9;
    }
    return { segs };
  }
  const lines = [];
  for (let i = 0; i < rows; i++) lines.push(newLine());
  let offset = 0;

  function rr(x, y, ww, hh, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + ww, y, x + ww, y + hh, r);
    ctx.arcTo(x + ww, y + hh, x, y + hh, r);
    ctx.arcTo(x, y + hh, x, y, r);
    ctx.arcTo(x, y, x + ww, y, r);
    ctx.fill();
  }

  function draw(dt, speed, tintHex) {
    offset += speed * dt;
    if (offset >= lineH) {
      const k = Math.floor(offset / lineH);
      for (let i = 0; i < k; i++) { lines.shift(); lines.push(newLine()); }
      offset -= k * lineH;
    }
    // background + editor gutter
    ctx.fillStyle = "#070d18"; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#0c1626"; ctx.fillRect(0, 0, 20, h);
    // top title bar (window chrome)
    ctx.fillStyle = "#0e1a2c"; ctx.fillRect(0, 0, w, 14);
    for (let d = 0; d < 3; d++) {
      ctx.fillStyle = ["#ff5f57", "#febc2e", "#28c840"][d];
      ctx.beginPath(); ctx.arc(8 + d * 9, 7, 2.4, 0, 7); ctx.fill();
    }
    for (let i = 0; i < lines.length; i++) {
      const y = h - i * lineH + offset - lineH;
      if (y < 14 || y > h) continue;
      ctx.fillStyle = "#26405a"; ctx.fillRect(5, y - 7, 9, 3); // line number
      for (const s of lines[i].segs) { ctx.fillStyle = s.c; ctx.globalAlpha = 0.92; rr(s.x, y - 8, s.w, 7, 2.5); }
    }
    ctx.globalAlpha = 1;
    if (tintHex) { ctx.fillStyle = tintHex; ctx.globalAlpha = 0.12; ctx.fillRect(0, 14, w, h - 14); ctx.globalAlpha = 1; }
    // scanline sheen
    ctx.fillStyle = "rgba(255,255,255,0.03)";
    for (let yy = 14; yy < h; yy += 4) ctx.fillRect(0, yy, w, 1);
    texture.needsUpdate = true;
  }
  return { texture, draw };
}

function makeMonitor(scale = 1) {
  const g = new THREE.Group();
  const fw = 1.42 * scale, fh = 0.9 * scale;
  const frame = new THREE.Mesh(new THREE.BoxGeometry(fw, fh, 0.06), mat({ color: 0x0a121f, roughness: 0.4, metalness: 0.6 }));
  const code = makeCodeScreen();
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(fw - 0.1, fh - 0.1),
    new THREE.MeshBasicMaterial({ map: code.texture, toneMapped: false }));
  screen.position.z = 0.035;
  const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.07, 0.34, 12), mat({ color: 0x10202f, metalness: 0.7, roughness: 0.3 }));
  stand.position.y = -fh / 2 - 0.16;
  const base = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.18, 0.03, 18), mat({ color: 0x10202f, metalness: 0.7, roughness: 0.3 }));
  base.position.y = -fh / 2 - 0.33;
  g.add(frame, screen, stand, base);
  g.userData = { code, glow: screen };
  return g;
}

function makeAgent(suitColor = 0x1b2740) {
  const g = new THREE.Group();
  const skin = mat({ color: 0x9fd0ff, roughness: 0.5, metalness: 0.2 });
  const suit = mat({ color: suitColor, roughness: 0.6, metalness: 0.3, emissive: 0x0a1830, emissiveIntensity: 0.35 });
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.3, 0.46, 6, 12), suit);
  body.position.y = 0.92;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.24, 20, 20), skin);
  head.position.y = 1.44;
  const visor = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.09, 0.04),
    mat({ color: 0x07101c, emissive: 0x2ee6ff, emissiveIntensity: 1.2 }));
  visor.position.set(0, 1.46, 0.22);
  const armGeo = new THREE.CapsuleGeometry(0.075, 0.32, 4, 8);
  const lArm = new THREE.Mesh(armGeo, suit), rArm = new THREE.Mesh(armGeo, suit);
  lArm.position.set(-0.32, 0.98, 0.16); lArm.rotation.z = 0.5; lArm.rotation.x = -0.9;
  rArm.position.set(0.32, 0.98, 0.16); rArm.rotation.z = -0.5; rArm.rotation.x = -0.9;
  g.add(body, head, visor, lArm, rArm);
  g.userData = { head, lArm, rArm, visor };
  return g;
}

function makeChair() {
  const g = new THREE.Group();
  const seatMat = mat({ color: 0x26344f, roughness: 0.6, metalness: 0.3 });
  const seat = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.1, 0.56), seatMat);
  seat.position.y = 0.52;
  const back = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.7, 0.1), seatMat);
  back.position.set(0, 0.86, -0.26);
  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.4, 10), mat({ color: 0x0c141f, metalness: 0.8, roughness: 0.3 }));
  post.position.y = 0.28;
  g.add(seat, back, post);
  return g;
}

// A workstation: desk, chair, agent, one or two code monitors, a mug.
function makeWorkstation({ monitors = 2, suit = 0x2c4163, deskColor = 0x243650 } = {}) {
  const g = new THREE.Group();
  const desk = new THREE.Mesh(new THREE.BoxGeometry(2.9, 0.16, 1.35), mat({ color: deskColor, roughness: 0.5, metalness: 0.4 }));
  desk.position.y = 0.84;
  // desk edge light
  const edge = new THREE.Mesh(new THREE.BoxGeometry(2.9, 0.02, 0.02), mat({ color: 0x07101c, emissive: 0x3a6c9a, emissiveIntensity: 1.0 }));
  edge.position.set(0, 0.76, 0.66);
  for (const dx of [-1.25, 1.25]) {
    const leg = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.84, 1.1), mat({ color: 0x1a2640, metalness: 0.6, roughness: 0.4 }));
    leg.position.set(dx, 0.42, 0); g.add(leg);
  }
  g.add(desk, edge);

  const screens = [];
  const xs = monitors === 1 ? [0] : [-0.78, 0.78];
  xs.forEach((mx, i) => {
    const mon = makeMonitor(monitors === 1 ? 1.15 : 1);
    mon.position.set(mx, 1.46, -0.46);
    mon.rotation.y = monitors === 1 ? 0 : (-mx * 0.28);
    g.add(mon);
    screens.push(mon.userData.code);
  });

  // keyboard
  const kb = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.03, 0.26), mat({ color: 0x0c1622, roughness: 0.6 }));
  kb.position.set(0, 0.93, 0.34); g.add(kb);
  // coffee mug
  const mug = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.06, 0.14, 12), mat({ color: 0xdfe6f0, roughness: 0.4 }));
  mug.position.set(0.95, 0.99, 0.3); g.add(mug);

  const agent = makeAgent(suit);
  agent.position.set(0, 0, 0.95);
  agent.rotation.y = Math.PI;
  g.add(agent);

  const chair = makeChair();
  chair.position.set(0, 0, 1.05); chair.rotation.y = Math.PI;
  g.add(chair);

  g.userData = { screens, agent };
  return g;
}

// A GPU server rack: cabinet, glowing cards, spinning fans.
function makeRig() {
  const g = new THREE.Group();
  const cabinet = new THREE.Mesh(new THREE.BoxGeometry(1.05, 2.0, 0.85), mat({ color: 0x1c2c46, roughness: 0.5, metalness: 0.6 }));
  cabinet.position.y = 1.0;
  g.add(cabinet);
  const cards = [], fans = [];
  for (let i = 0; i < 5; i++) {
    const y = 0.45 + i * 0.32;
    const card = new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.22, 0.9), mat({ color: 0x0a1118, roughness: 0.4, metalness: 0.8 }));
    card.position.set(0, y, 0); g.add(card);
    const led = new THREE.Mesh(new THREE.BoxGeometry(1.12, 0.04, 0.02), mat({ color: 0x07101c, emissive: PALETTE.idle, emissiveIntensity: 0.4 }));
    led.position.set(0, y, 0.45); g.add(led); cards.push(led);
    for (const fx of [-0.28, 0.28]) {
      const fan = new THREE.Group();
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.018, 8, 16), mat({ color: 0x16222f, metalness: 0.6, roughness: 0.4 }));
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.04, 10), mat({ color: 0x223344 }));
      hub.rotation.x = Math.PI / 2;
      const bladeMat = mat({ color: 0x3a5168, roughness: 0.6 });
      for (let b = 0; b < 5; b++) {
        const blade = new THREE.Mesh(new THREE.BoxGeometry(0.014, 0.085, 0.02), bladeMat);
        blade.position.y = 0.045;
        const pivot = new THREE.Group(); pivot.add(blade); pivot.rotation.z = (b / 5) * Math.PI * 2;
        fan.add(pivot);
      }
      fan.add(ring, hub); fan.position.set(fx, y, 0.47); g.add(fan); fans.push(fan);
    }
  }
  g.userData = { cards, fans };
  return g;
}

// A low-poly potted plant for warmth.
function makePlant(scale = 1) {
  const g = new THREE.Group();
  const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.16, 0.32, 12), mat({ color: 0x2a3346, roughness: 0.8 }));
  pot.position.y = 0.16;
  g.add(pot);
  const leaf = mat({ color: 0x2f7d54, roughness: 0.8, emissive: 0x0a2417, emissiveIntensity: 0.25 });
  for (let i = 0; i < 7; i++) {
    const blade = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.62 + Math.random() * 0.3, 5), leaf);
    const a = (i / 7) * Math.PI * 2;
    blade.position.set(Math.cos(a) * 0.08, 0.55, Math.sin(a) * 0.08);
    blade.rotation.set((Math.random() - 0.5) * 0.5, a, (Math.random() - 0.5) * 0.5);
    g.add(blade);
  }
  g.scale.setScalar(scale);
  return g;
}

function makeCeilingBar(x, z, len = 3) {
  const g = new THREE.Group();
  const housing = new THREE.Mesh(new THREE.BoxGeometry(len, 0.08, 0.18), mat({ color: 0x0a121d, metalness: 0.5, roughness: 0.5 }));
  const glow = new THREE.Mesh(new THREE.BoxGeometry(len - 0.1, 0.04, 0.12), new THREE.MeshBasicMaterial({ color: 0xbfe0ff, toneMapped: false }));
  glow.position.y = -0.05;
  g.add(housing, glow);
  g.position.set(x, 5.0, z);
  return g;
}

function init(canvas) {
  if (R || !canvas) return;
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
  } catch (e) {
    const s = document.getElementById("office-status");
    if (s) s.textContent = "3D unavailable (no WebGL)";
    return;
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x0d1626, 0.022);

  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 120);
  camera.position.set(8.2, 5.6, 9.6);
  camera.lookAt(0, 1.1, 0);

  const root = new THREE.Group();
  scene.add(root);

  // ── lighting: bright sky/ground hemi + ambient + warm key + accents + fills ──
  scene.add(new THREE.HemisphereLight(0xdfeaff, 0x2a3550, 1.15)); // soft global daylight
  scene.add(new THREE.AmbientLight(0x6f86b0, 1.0));
  const key = new THREE.DirectionalLight(0xe9f4ff, 1.25); key.position.set(5, 9, 6); scene.add(key);
  const warm = new THREE.DirectionalLight(0xffe6c2, 0.7); warm.position.set(-7, 5, 3); scene.add(warm);
  // soft fills from the camera side so the crew & desks read clearly
  const fill = new THREE.PointLight(0xd8e8ff, 1.4, 34); fill.position.set(4, 4.5, 8); scene.add(fill);
  const fill2 = new THREE.PointLight(0xcfe0ff, 1.0, 30); fill2.position.set(-4, 4, 7); scene.add(fill2);
  const accent = new THREE.PointLight(0x2ee6ff, 1.5, 22); accent.position.set(0, 2.6, -0.4); scene.add(accent);
  const rigLight = new THREE.PointLight(0x2ee6ff, 0.9, 18); rigLight.position.set(-6, 2.4, -1.5); scene.add(rigLight);

  // ── floor + neon grid + soft rug ──
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(46, 46), mat({ color: 0x1a2740, roughness: 0.82, metalness: 0.12 }));
  floor.rotation.x = -Math.PI / 2; root.add(floor);
  const grid = new THREE.GridHelper(46, 46, 0x5fd0ff, 0x2a4a6a);
  grid.material.transparent = true; grid.material.opacity = 0.35; root.add(grid);
  const rug = new THREE.Mesh(new THREE.CircleGeometry(3.6, 40), mat({ color: 0x223451, roughness: 0.92 }));
  rug.rotation.x = -Math.PI / 2; rug.position.y = 0.01; root.add(rug);

  // ── back + side walls with neon strips ──
  const wallMat = mat({ color: 0x18223a, roughness: 0.9, metalness: 0.06 });
  const backWall = new THREE.Mesh(new THREE.PlaneGeometry(46, 10), wallMat);
  backWall.position.set(0, 5, -9); root.add(backWall);
  const sideWall = new THREE.Mesh(new THREE.PlaneGeometry(46, 10), wallMat);
  sideWall.rotation.y = Math.PI / 2; sideWall.position.set(-9, 5, 0); root.add(sideWall);
  for (let i = 0; i < 4; i++) {
    const strip = new THREE.Mesh(new THREE.BoxGeometry(10, 0.04, 0.04), mat({ color: 0x07101c, emissive: 0x2050ff, emissiveIntensity: 1 }));
    strip.position.set(-2, 1.2 + i * 1.0, -8.9); root.add(strip);
  }

  // ── GPU rig wall (left) — four racks under load ──
  const rigs = [];
  for (let i = 0; i < 4; i++) {
    const rig = makeRig();
    rig.position.set(-6.4, 0, -2.6 + i * 1.7);
    rig.rotation.y = Math.PI / 2;
    root.add(rig); rigs.push(rig);
  }

  // ── workstations (a small crew) ──
  const screens = [], agents = [];
  const stations = [
    { pos: [0, 0, 0.2], rot: 0, opts: { monitors: 2, suit: 0x33507e } },          // hero, faces camera-ish
    { pos: [3.6, 0, 1.4], rot: -0.7, opts: { monitors: 2, suit: 0x3a6450 } },
    { pos: [2.0, 0, 3.6], rot: -1.15, opts: { monitors: 1, suit: 0x6a4060 } },
    { pos: [-2.4, 0, 2.6], rot: 0.7, opts: { monitors: 1, suit: 0x4a4070 } },
  ];
  let hero = null;
  for (const st of stations) {
    const ws = makeWorkstation(st.opts);
    ws.position.set(...st.pos); ws.rotation.y = st.rot;
    root.add(ws);
    screens.push(...ws.userData.screens);
    agents.push(ws.userData.agent);
    if (!hero) hero = ws;
  }

  // ── plants for warmth ──
  for (const [x, z, s] of [[-7.6, 2.8, 1.2], [6.6, -1.2, 1.1], [5.4, 4.2, 1.0], [-3.6, -1.2, 0.9], [7.4, 2.0, 1.0]]) {
    const p = makePlant(s); p.position.set(x, 0, z); root.add(p);
  }

  // ── ceiling light bars ──
  root.add(makeCeilingBar(0, -2, 4), makeCeilingBar(-4, 1, 3.4), makeCeilingBar(3, 3, 3));

  // ── hero "build ring" hologram above the main desk ──
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.5, 0.045, 12, 48),
    mat({ color: 0x07101c, emissive: PALETTE.idle, emissiveIntensity: 1.2 }));
  ring.position.set(0, 2.7, -0.3); ring.rotation.x = Math.PI / 2; root.add(ring);
  const ring2 = new THREE.Mesh(new THREE.TorusGeometry(0.66, 0.02, 10, 48),
    mat({ color: 0x07101c, emissive: PALETTE.idle, emissiveIntensity: 0.8 }));
  ring2.position.copy(ring.position); ring2.rotation.x = Math.PI / 2.4; root.add(ring2);

  // ── floating particles ──
  const pcount = 320;
  const ppos = new Float32Array(pcount * 3);
  for (let i = 0; i < pcount; i++) {
    ppos[i * 3] = (Math.random() - 0.5) * 18;
    ppos[i * 3 + 1] = Math.random() * 7;
    ppos[i * 3 + 2] = (Math.random() - 0.5) * 18;
  }
  const pgeo = new THREE.BufferGeometry();
  pgeo.setAttribute("position", new THREE.BufferAttribute(ppos, 3));
  const particles = new THREE.Points(pgeo, new THREE.PointsMaterial({ color: 0x2ee6ff, size: 0.04, transparent: true, opacity: 0.55 }));
  root.add(particles);

  const clock = new THREE.Clock();
  R = {
    renderer, scene, camera, root, screens, agents, rigs, ring, ring2, particles, accent, rigLight, clock,
    mode: "idle", tintHex: TINT_HEX.idle, targetColor: new THREE.Color(PALETTE.idle),
    autoRot: 0.12, dragRot: 0, dragging: false, lastX: 0, raf: 0, frame: 0,
  };

  canvas.style.touchAction = "none";
  canvas.addEventListener("pointerdown", (e) => { R.dragging = true; R.lastX = e.clientX; });
  window.addEventListener("pointerup", () => { R.dragging = false; });
  window.addEventListener("pointermove", (e) => {
    if (!R.dragging) return;
    R.dragRot += (e.clientX - R.lastX) * 0.005; R.lastX = e.clientX;
  });

  resize();
  window.addEventListener("resize", resize);
  R.renderer.render(R.scene, R.camera);
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
  const dt = Math.min(R.clock.getDelta(), 0.05);
  R.frame++;
  const building = R.mode === "building";
  const speed = building ? 9 : 1.2;
  const amp = building ? 0.5 : 0.06;

  // agents type (staggered so the crew isn't in lockstep)
  R.agents.forEach((ag, i) => {
    const a = ag.userData; const ph = i * 1.3;
    a.lArm.rotation.x = -0.9 + Math.sin(t * speed + ph) * amp;
    a.rArm.rotation.x = -0.9 + Math.sin(t * speed + ph + 1.1) * amp;
    a.head.position.y = 1.44 + Math.sin(t * 1.6 + ph) * 0.015;
    ag.position.y = Math.sin(t * 1.2 + ph) * 0.02;
  });

  // monitors: scroll code (throttled to every other frame for perf)
  if (R.frame % 2 === 0) {
    const codeSpeed = building ? 130 : 26;
    for (const sc of R.screens) sc.draw(dt * 2, codeSpeed, building ? null : R.tintHex);
  }

  // rings + particles
  R.ring.rotation.z += building ? 0.08 : 0.01;
  R.ring2.rotation.z -= building ? 0.05 : 0.008;
  R.particles.rotation.y += building ? 0.0016 : 0.0004;

  // GPU rigs: fans spin + cards glow under load
  const fanSpin = building ? 0.55 : 0.05;
  const ledTarget = building ? 1.7 + Math.sin(t * 5) * 0.25 : 0.35;
  for (const rig of R.rigs) {
    const rd = rig.userData;
    for (const fan of rd.fans) fan.rotation.z += fanSpin;
    for (const led of rd.cards) {
      led.material.emissive.lerp(R.targetColor, 0.08);
      led.material.emissiveIntensity += (ledTarget - led.material.emissiveIntensity) * 0.1;
    }
  }

  // color lerp on rings + accent lights
  R.ring.material.emissive.lerp(R.targetColor, 0.08);
  R.ring2.material.emissive.lerp(R.targetColor, 0.08);
  R.accent.color.lerp(R.targetColor, 0.08);
  R.rigLight.color.lerp(R.targetColor, 0.08);

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
  R.autoRot = mode === "building" ? 0.05 : 0.13;
  R.targetColor.set(PALETTE[mode]);
  R.tintHex = TINT_HEX[mode];

  const statusEl = document.getElementById("office-status");
  const subEl = document.getElementById("office-sub");
  if (statusEl) {
    const labels = { idle: "Idle — awaiting work", building: "Building…", passed: "Build passed", failed: "Build failed" };
    statusEl.textContent = labels[mode];
    statusEl.dataset.mode = mode;
  }
  if (subEl) {
    const bits = [];
    if (c.name) bits.push(c.name);
    if (c.tokens) bits.push(`${c.tokens} tokens`);
    if (c.tok_per_sec) bits.push(`${c.tok_per_sec} tok/s`);
    if (c.elapsed) bits.push(`${c.elapsed}s`);
    subEl.textContent = bits.join("  ·  ");
  }
}

window.OfficeView = { init, setState, resize };

(function autostart() {
  const panel = document.getElementById("panel-office");
  if (panel && !panel.hidden) {
    init(document.getElementById("office-canvas"));
    setTimeout(resize, 50);
  }
})();

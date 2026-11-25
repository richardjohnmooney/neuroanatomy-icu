// 3D model panel: three.js scene of the structure meshes (MNI mm, RAS) with a slice plane linked to the CT.
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { AppData } from '../data';
import { displayName } from '../data';
import type { Arrow, CameraView, Structure, Vec3 } from '../types';
import { setState, state, subscribe } from '../state';
import type { AppState } from '../state';

export interface Model3DOptions { container: HTMLElement; data: AppData; readout: HTMLElement; }

interface Entry {
  structure: Structure;
  root: THREE.Group;
  meshes: THREE.Mesh[];
  centre: THREE.Vector3;   // MNI mm, rest position
  deep: boolean;
  opacity?: number;        // current 3D opacity from the store (0 = hidden)
}

const BRAIN_CENTRE_MNI = new THREE.Vector3(0, -18, 15);
const EXPLODE_MM = 25;
/** Below this 3D opacity a structure is context only: not pickable, no highlight glow. */
const PICK_MIN_OPACITY = 0.2;
/** At or above this a structure is treated as solid when choosing what the cursor points at. */
const SOLID_OPACITY = 0.6;
const SLICE_COLOR = 0x4fb3ff;

export async function createModel3D(o: Model3DOptions): Promise<{ dispose(): void }> {
  const { container, data, readout } = o;

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x07090c, 1);
  renderer.localClippingEnabled = true;
  renderer.domElement.style.display = 'block';
  container.append(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 5, 2000);
  scene.add(camera);

  scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x2a2420, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.3);
  key.position.set(1, 2, 1.5);
  scene.add(key);
  const headlight = new THREE.DirectionalLight(0xffffff, 0.9);
  headlight.position.set(0, 0, 1);
  headlight.target.position.set(0, 0, -1);
  camera.add(headlight);
  camera.add(headlight.target);

  // MNI RAS -> three y-up: (x,y,z) -> (x, z, -y). Anterior therefore points to -z in three.
  const world = new THREE.Group();
  world.rotation.x = -Math.PI / 2;
  world.updateMatrixWorld(true);
  scene.add(world);
  const meshHost = new THREE.Group();
  world.add(meshHost);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  const target = BRAIN_CENTRE_MNI.clone().applyMatrix4(world.matrixWorld);
  controls.target.copy(target);
  // oblique anterolateral-superior view from patient right (appears on screen left)
  camera.position.copy(target).add(new THREE.Vector3(190, 150, -290));
  controls.update();

  // clip plane (three world space), mutated in place
  const clipPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);

  // ---- slice plane marker (lives in MNI space inside `world`)
  const sliceGroup = new THREE.Group();
  world.add(sliceGroup);
  const sliceMat = new THREE.MeshBasicMaterial({
    color: SLICE_COLOR, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false,
  });
  const sliceLineMat = new THREE.LineBasicMaterial({ color: SLICE_COLOR, transparent: true, opacity: 0.9, depthWrite: false });
  const sliceMesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), sliceMat);
  const sliceLine = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(1, 1)), sliceLineMat);
  sliceMesh.renderOrder = 10;
  sliceLine.renderOrder = 11;
  sliceGroup.add(sliceMesh, sliceLine);

  // ---- annotation arrows (MNI space, drawn over everything)
  const arrowGroup = new THREE.Group();
  world.add(arrowGroup);
  function buildArrows(arrows: Arrow[]): void {
    for (const c of [...arrowGroup.children]) {
      arrowGroup.remove(c);
      c.traverse((n) => {
        const m = n as THREE.Mesh;
        if (m.isMesh) { m.geometry.dispose(); (m.material as THREE.Material).dispose(); }
      });
    }
    for (const a of arrows) {
      if (a.no3d) continue;
      const from = new THREE.Vector3(...a.from), to = new THREE.Vector3(...a.to);
      const dir = to.clone().sub(from);
      const len = dir.length();
      if (len < 1e-3) continue;
      dir.normalize();
      const head = Math.min(9, len * 0.45), r = 1.6;
      const mat = () => new THREE.MeshBasicMaterial({ color: new THREE.Color(a.color ?? '#ffffff'), depthTest: false, transparent: true, opacity: 0.95 });
      const shaft = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len - head, 12), mat());
      const cone = new THREE.Mesh(new THREE.ConeGeometry(r * 2.6, head, 16), mat());
      shaft.position.y = (len - head) / 2;
      cone.position.y = len - head / 2;
      const g = new THREE.Group();
      g.add(shaft, cone);
      g.position.copy(from);
      g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
      for (const m of [shaft, cone]) m.renderOrder = 30;
      arrowGroup.add(g);
    }
    requestRender();
  }

  // ---- camera requests (tutorials): glide to a view; user orbiting never writes back
  let camAnim: { t0: number; dur: number; fromPos: THREE.Vector3; fromTgt: THREE.Vector3; toDir: THREE.Vector3; toDist: number; toTgt: THREE.Vector3; fromDir: THREE.Vector3; fromDist: number } | null = null;
  const fitDistance = () => {
    const vHalf = THREE.MathUtils.degToRad(camera.fov / 2);
    const hHalf = Math.atan(Math.tan(vHalf) * camera.aspect);
    return 95 / Math.sin(Math.min(vHalf, hHalf));
  };
  function requestCamera(v: CameraView): void {
    const toTgt = new THREE.Vector3(...(v.target ?? [BRAIN_CENTRE_MNI.x, BRAIN_CENTRE_MNI.y, BRAIN_CENTRE_MNI.z])).applyMatrix4(world.matrixWorld);
    // direction is a vector in MNI space: rotate it (no translation) into three space
    const toDir = new THREE.Vector3(...v.dir).applyQuaternion(world.quaternion).normalize();
    // OrbitControls can't look exactly along its up axis: tilt a pure top/bottom view slightly (anterior up)
    if (Math.abs(toDir.y) > 0.995) toDir.set(0, Math.sign(toDir.y), 0.08).normalize();
    const fromDir = camera.position.clone().sub(controls.target);
    const fromDist = fromDir.length();
    fromDir.normalize();
    camAnim = {
      t0: performance.now(), dur: 900, fromPos: camera.position.clone(), fromTgt: controls.target.clone(),
      toDir, toDist: fitDistance() / Math.max(0.2, v.zoom ?? 1), toTgt, fromDir, fromDist,
    };
  }
  function stepCamera(now: number): void {
    if (!camAnim) return;
    const a = camAnim;
    const t = Math.min(1, (now - a.t0) / a.dur);
    const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; // ease in-out cubic
    const tgt = a.fromTgt.clone().lerp(a.toTgt, e);
    // slerp the viewing direction so the camera swings round the brain rather than through it
    const q = new THREE.Quaternion().setFromUnitVectors(a.fromDir, a.toDir);
    const dir = a.fromDir.clone().applyQuaternion(new THREE.Quaternion().slerp(q, e));
    const dist = a.fromDist + (a.toDist - a.fromDist) * e;
    camera.position.copy(tgt).addScaledVector(dir, dist);
    controls.target.copy(tgt);
    controls.update();
    requestRender();
    if (t >= 1) camAnim = null;
  }
  const stopCamAnim = () => { camAnim = null; };
  renderer.domElement.addEventListener('pointerdown', stopCamAnim);
  renderer.domElement.addEventListener('wheel', stopCamAnim, { passive: true });

  // ---- load meshes
  const entries: Entry[] = [];
  const materials = new Map<string, THREE.MeshStandardMaterial>();
  const meshToEntry = new Map<THREE.Object3D, Entry>();
  readout.textContent = 'Loading…';

  for (const s of data.manifest.structures) {
    materials.set(s.id, new THREE.MeshStandardMaterial({
      color: new THREE.Color(s.color), roughness: 0.45, metalness: 0.05, emissive: new THREE.Color(0x000000),
    }));
  }

  const loader = new GLTFLoader();
  const jobs: Promise<void>[] = [];
  for (const s of data.manifest.structures) {
    const mat = materials.get(s.id)!;
    const deep = s.group === 'deep-grey' || s.group === 'csf';
    for (const file of s.meshes) {
      jobs.push(loader.loadAsync(data.url('meshes/' + file)).then((gltf) => {
        const root = new THREE.Group();
        const meshes: THREE.Mesh[] = [];
        gltf.scene.traverse((n) => {
          const m = n as THREE.Mesh;
          if (!m.isMesh) return;
          if (!m.geometry.getAttribute('normal')) m.geometry.computeVertexNormals();
          m.material = mat;
          meshes.push(m);
        });
        root.add(gltf.scene);
        root.updateMatrixWorld(true);
        // centroid in MNI space (rest position): bbox centre in world, back-transformed
        const box = new THREE.Box3().setFromObject(root);
        const centre = world.worldToLocal(box.getCenter(new THREE.Vector3()));
        const entry: Entry = { structure: s, root, meshes, centre, deep };
        for (const m of meshes) meshToEntry.set(m, entry);
        entries.push(entry);
        meshHost.add(root);
      }).catch((e) => console.warn('model3d: failed to load mesh', file, e)));
    }
  }

  let dirty = true;
  const requestRender = () => { dirty = true; };
  controls.addEventListener('change', requestRender);

  const tmpColor = new THREE.Color();

  function apply(s: AppState) {
    const [cx, cy, cz] = s.crosshair;
    // clip plane in three world space: (x,y,z)mni -> (x, z, -y)
    if (s.plane === 'axial') { clipPlane.normal.set(0, -1, 0); clipPlane.constant = cz; }         // keep z <= cz
    else if (s.plane === 'coronal') { clipPlane.normal.set(0, 0, 1); clipPlane.constant = cy; }   // keep y <= cy
    else { clipPlane.normal.set(-1, 0, 0); clipPlane.constant = cx; }                             // keep x <= cx

    // slice marker, centred on the brain in its in-plane axes
    sliceGroup.visible = s.slicePlane3d;
    sliceGroup.rotation.set(0, 0, 0);
    if (s.plane === 'axial') { sliceGroup.position.set(0, -18, cz); sliceGroup.scale.set(190, 230, 1); }
    else if (s.plane === 'coronal') { sliceGroup.position.set(0, cy, 15); sliceGroup.rotation.x = Math.PI / 2; sliceGroup.scale.set(190, 150, 1); }
    else { sliceGroup.position.set(cx, -18, 15); sliceGroup.rotation.y = Math.PI / 2; sliceGroup.scale.set(150, 230, 1); }

    const sel = s.selectedId;
    const clip = s.clip3d;
    for (const [id, m] of materials) {
      const st = data.byId.get(id)!;
      const isSel = id === sel;
      const isHover = id === s.hoverId;
      const base = s.opacity3d[id] ?? 0; // 3D is driven only by the opacity sliders
      const faint = base < PICK_MIN_OPACITY;
      const fade = sel !== null && !isSel;
      let op = base;
      if (fade) op = Math.min(op, 0.3); // selection fades the others further
      m.transparent = op < 1;
      m.opacity = op;
      m.depthWrite = op >= 1;
      m.side = clip ? THREE.DoubleSide : THREE.FrontSide;
      m.clippingPlanes = clip ? [clipPlane] : null;
      m.emissive.set(st.color);
      m.emissiveIntensity = faint ? 0 : isSel ? 0.45 : isHover ? 0.3 : 0;
      tmpColor.set(st.color);
      if (isHover && !isSel) tmpColor.lerp(new THREE.Color(0xffffff), 0.3);
      m.color.copy(tmpColor);
      m.needsUpdate = true;
    }
    for (const e of entries) {
      const sid = e.structure.id;
      const op = s.opacity3d[sid] ?? 0;
      e.root.visible = op > 0;
      e.opacity = op;
      const isSel = sid === sel;
      // translucent volumes first, then shells/deep things, selected last
      for (const m of e.meshes) m.renderOrder = op < 1 ? 0 : isSel ? 5 : e.deep ? 2 : 1;
      // explode
      const dir = e.centre.clone().sub(BRAIN_CENTRE_MNI);
      const dist = dir.length();
      if (dist > 1e-3) dir.multiplyScalar(1 / dist);
      const amt = EXPLODE_MM * s.explode * (e.deep ? 0.4 : 1) * Math.min(1, dist / 25);
      e.root.position.copy(dir.multiplyScalar(amt));
    }
    requestRender();
  }

  // ---- picking
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const tmp = new THREE.Vector3();

  function pick(ev: PointerEvent): { entry: Entry; mni: Vec3 } | null {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    const targets: THREE.Object3D[] = [];
    // Very faint structures aren't pickable at all. Among the rest, prefer the nearest fairly solid
    // structure under the cursor, so translucent volumes (e.g. territories) don't block the arteries
    // inside them; fall back to the nearest translucent one.
    for (const e of entries) if (e.root.visible && (e.opacity ?? 0) >= PICK_MIN_OPACITY) targets.push(...e.meshes);
    const hits = raycaster.intersectObjects(targets, false);
    let fallback: { entry: Entry; point: THREE.Vector3 } | null = null;
    let chosen: { entry: Entry; point: THREE.Vector3 } | null = null;
    for (const h of hits) {
      if (state.clip3d && clipPlane.distanceToPoint(h.point) < 0) continue;
      const entry = meshToEntry.get(h.object);
      if (!entry) continue;
      if ((entry.opacity ?? 0) >= SOLID_OPACITY) { chosen = { entry, point: h.point }; break; }
      if (!fallback) fallback = { entry, point: h.point };
    }
    const pickHit = chosen ?? fallback;
    if (!pickHit) return null;
    tmp.copy(pickHit.point);
    world.worldToLocal(tmp);
    tmp.sub(pickHit.entry.root.position);
    return { entry: pickHit.entry, mni: [tmp.x, tmp.y, tmp.z] };
  }

  let moveEv: PointerEvent | null = null;
  let moveRaf = 0;
  function onMove() {
    moveRaf = 0;
    if (!moveEv) return;
    const hit = pick(moveEv);
    moveEv = null;
    readout.textContent = hit ? displayName(hit.entry.structure, hit.mni) : '';
    setState({ hoverId: hit ? hit.entry.structure.id : null }, '3d');
  }
  const el = renderer.domElement;
  const onPointerMove = (ev: PointerEvent) => {
    if (ev.buttons) return;
    moveEv = ev;
    if (!moveRaf) moveRaf = requestAnimationFrame(onMove);
  };
  const onPointerLeave = () => {
    moveEv = null;
    readout.textContent = '';
    setState({ hoverId: null }, '3d');
  };
  let down: { x: number; y: number } | null = null;
  const onDown = (ev: PointerEvent) => { down = { x: ev.clientX, y: ev.clientY }; };
  const onUp = (ev: PointerEvent) => {
    if (!down) return;
    const moved = Math.hypot(ev.clientX - down.x, ev.clientY - down.y);
    down = null;
    if (moved >= 4) return;
    const hit = pick(ev);
    if (hit) setState({ selectedId: hit.entry.structure.id, crosshair: hit.mni }, '3d');
  };
  el.addEventListener('pointermove', onPointerMove);
  el.addEventListener('pointerleave', onPointerLeave);
  el.addEventListener('pointerdown', onDown);
  el.addEventListener('pointerup', onUp);

  // ---- resize
  let lastFit = 0; // fit distance at the previous resize: zoom relative to it is kept across resizes
  const resize = () => {
    const w = Math.max(1, container.clientWidth), h = Math.max(1, container.clientHeight);
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // Keep the whole brain (radius ~95 mm) in frame for any panel shape: fit to the narrower FOV,
    // keeping the current viewing direction.
    const vHalf = THREE.MathUtils.degToRad(camera.fov / 2);
    const hHalf = Math.atan(Math.tan(vHalf) * camera.aspect);
    const fit = 95 / Math.sin(Math.min(vHalf, hHalf));
    // first fit: whole brain; later resizes keep the current zoom relative to the fit (tutorial close-ups)
    const zoomRatio = lastFit > 0 ? camera.position.distanceTo(controls.target) / lastFit : 1;
    const fitDist = fit * zoomRatio;
    lastFit = fit;
    const dir = camera.position.clone().sub(controls.target).normalize();
    camera.position.copy(controls.target).addScaledVector(dir, fitDist);
    controls.update();
    requestRender();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();

  // ---- store
  const relevant: (keyof AppState)[] = ['crosshair', 'plane', 'clip3d', 'explode', 'visible', 'opacity3d', 'selectedId', 'hoverId', 'slicePlane3d'];
  const unsub = subscribe((s, changed) => {
    if (changed.has('arrows')) buildArrows(s.arrows);
    if (changed.has('camera3d') && s.camera3d) requestCamera(s.camera3d);
    if (!relevant.some((k) => changed.has(k))) return;
    apply(s);
  });

  // ---- render loop (on demand)
  let raf = 0;
  const loop = (now: number) => {
    raf = requestAnimationFrame(loop);
    stepCamera(now);
    controls.update(); // fires 'change' while damping
    if (dirty) { dirty = false; renderer.render(scene, camera); }
  };
  raf = requestAnimationFrame(loop);

  await Promise.all(jobs);
  readout.textContent = '';
  apply(state);
  buildArrows(state.arrows);
  if (state.camera3d) requestCamera(state.camera3d);

  return {
    dispose() {
      cancelAnimationFrame(raf);
      if (moveRaf) cancelAnimationFrame(moveRaf);
      unsub();
      ro.disconnect();
      controls.dispose();
      el.removeEventListener('pointermove', onPointerMove);
      el.removeEventListener('pointerleave', onPointerLeave);
      el.removeEventListener('pointerdown', onDown);
      el.removeEventListener('pointerup', onUp);
      el.removeEventListener('pointerdown', stopCamAnim);
      el.removeEventListener('wheel', stopCamAnim);
      buildArrows([]);
      for (const m of materials.values()) m.dispose();
      for (const e of entries) for (const m of e.meshes) m.geometry.dispose();
      renderer.dispose();
      el.remove();
    },
  };
}

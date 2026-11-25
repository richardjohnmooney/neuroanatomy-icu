// 3D model panel: three.js scene of the structure meshes (MNI mm, RAS) with a slice plane linked to the CT.
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { AppData } from '../data';
import { displayName } from '../data';
import type { Structure, Vec3 } from '../types';
import { setState, state, subscribe } from '../state';
import type { AppState } from '../state';

export interface Model3DOptions { container: HTMLElement; data: AppData; readout: HTMLElement; }

interface Entry {
  structure: Structure;
  root: THREE.Group;
  meshes: THREE.Mesh[];
  centre: THREE.Vector3;   // MNI mm, rest position
  deep: boolean;
  ghost?: boolean;         // currently shown only as translucent context (not pickable)
}

const BRAIN_CENTRE_MNI = new THREE.Vector3(0, -18, 15);
const EXPLODE_MM = 25;
/** Opacity of context ("ghost") structures — faint enough that the topic's structures dominate. */
const GHOST_OPACITY = 0.12;
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
      const ghost = !s.visible.has(id) && s.ghost.has(id); // context-only structure
      const fade = sel !== null && !isSel;
      m.transparent = ghost || fade;
      m.opacity = ghost ? GHOST_OPACITY : fade ? 0.3 : 1;
      m.depthWrite = !(ghost || fade);
      m.side = clip ? THREE.DoubleSide : THREE.FrontSide;
      m.clippingPlanes = clip ? [clipPlane] : null;
      m.emissive.set(st.color);
      m.emissiveIntensity = ghost ? 0 : isSel ? 0.45 : isHover ? 0.3 : 0;
      tmpColor.set(st.color);
      if (isHover && !isSel) tmpColor.lerp(new THREE.Color(0xffffff), 0.3);
      m.color.copy(tmpColor);
      m.needsUpdate = true;
    }
    for (const e of entries) {
      const sid = e.structure.id;
      const focus = s.visible.has(sid);
      e.root.visible = focus || s.ghost.has(sid);
      e.ghost = !focus;
      const isSel = sid === sel;
      // ghosts first, then shells/deep things, selected last, so focus structures show through context
      for (const m of e.meshes) m.renderOrder = e.ghost ? 0 : isSel ? 5 : e.deep ? 2 : 1;
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
    for (const e of entries) if (e.root.visible && !e.ghost) targets.push(...e.meshes); // context isn't pickable
    const hits = raycaster.intersectObjects(targets, false);
    for (const h of hits) {
      if (state.clip3d && clipPlane.distanceToPoint(h.point) < 0) continue;
      const entry = meshToEntry.get(h.object);
      if (!entry) continue;
      tmp.copy(h.point);
      world.worldToLocal(tmp);
      tmp.sub(entry.root.position);
      return { entry, mni: [tmp.x, tmp.y, tmp.z] };
    }
    return null;
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
  const resize = () => {
    const w = Math.max(1, container.clientWidth), h = Math.max(1, container.clientHeight);
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // Keep the whole brain (radius ~95 mm) in frame for any panel shape: fit to the narrower FOV,
    // keeping the current viewing direction.
    const vHalf = THREE.MathUtils.degToRad(camera.fov / 2);
    const hHalf = Math.atan(Math.tan(vHalf) * camera.aspect);
    const fitDist = 95 / Math.sin(Math.min(vHalf, hHalf));
    const dir = camera.position.clone().sub(controls.target).normalize();
    camera.position.copy(controls.target).addScaledVector(dir, fitDist);
    controls.update();
    requestRender();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();

  // ---- store
  const relevant: (keyof AppState)[] = ['crosshair', 'plane', 'clip3d', 'explode', 'visible', 'ghost', 'selectedId', 'hoverId'];
  const unsub = subscribe((s, changed) => {
    if (!relevant.some((k) => changed.has(k))) return;
    apply(s);
  });

  // ---- render loop (on demand)
  let raf = 0;
  const loop = () => {
    raf = requestAnimationFrame(loop);
    controls.update(); // fires 'change' while damping
    if (dirty) { dirty = false; renderer.render(scene, camera); }
  };
  raf = requestAnimationFrame(loop);

  await Promise.all(jobs);
  readout.textContent = '';
  apply(state);

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
      for (const m of materials.values()) m.dispose();
      for (const e of entries) for (const m of e.meshes) m.geometry.dispose();
      renderer.dispose();
      el.remove();
    },
  };
}

// First-person Gaussian-splat viewer (three + Spark). Loads one .ply by URL and
// gives WASD + Q/E movement with pointer-lock mouse-look. The camera control
// logic matches the local tool's viewer (multi-scene-manager.js).

import * as THREE from 'three';
import { SplatMesh, SparkRenderer } from '@sparkjsdev/spark';
import { api } from './api.js';

class Viewer {
  constructor(container) {
    this.container = container;
    this.cameraPosition = new THREE.Vector3(0, 1.6, 3);
    this.cameraRotation = new THREE.Euler(0, 0, 0);
    this.moveSpeed = 0.15;
    this.lookSpeed = 0.002;
    this.keys = {};
    this.pointerLocked = false;
  }

  init() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0a0a0a);

    this.camera = new THREE.PerspectiveCamera(
      75,
      Math.max(1, this.container.clientWidth) / Math.max(1, this.container.clientHeight),
      0.05, 5000,
    );
    this.camera.position.copy(this.cameraPosition);

    this.renderer = new THREE.WebGLRenderer({ antialias: false });
    this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.container.appendChild(this.renderer.domElement);

    this.sparkRenderer = new SparkRenderer({ renderer: this.renderer, onDirty: () => {} });
    this.scene.add(this.sparkRenderer);
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.8));

    this._setupControls();
    window.addEventListener('resize', () => this._resize());
    this._animate();
  }

  async loadSplat(url) {
    const mesh = new SplatMesh({ url });
    this.scene.add(mesh);
    this.splat = mesh;
    // Recenter the camera near the scene centroid once the splat is ready, so
    // the user starts inside the capture rather than potentially outside it.
    try {
      if (mesh.initialized) await mesh.initialized;
      this._recenter(mesh);
    } catch { /* leave camera at the default pose */ }
    return mesh;
  }

  _recenter(mesh) {
    let n = 0;
    const sum = new THREE.Vector3();
    if (typeof mesh.forEachSplat !== 'function') return;
    mesh.forEachSplat((_i, center) => { sum.add(center); n++; });
    if (!n) return;
    sum.multiplyScalar(1 / n);
    this.cameraPosition.set(sum.x, sum.y, sum.z);
    this.camera.position.copy(this.cameraPosition);
  }

  _setupControls() {
    window.addEventListener('keydown', (e) => { this.keys[e.key.toLowerCase()] = true; });
    window.addEventListener('keyup', (e) => { this.keys[e.key.toLowerCase()] = false; });
    this.renderer.domElement.addEventListener('click', () => {
      this.renderer.domElement.requestPointerLock();
    });
    document.addEventListener('pointerlockchange', () => {
      this.pointerLocked = document.pointerLockElement === this.renderer.domElement;
    });
    document.addEventListener('mousemove', (e) => {
      if (!this.pointerLocked) return;
      this.cameraRotation.y -= e.movementX * this.lookSpeed;
      this.cameraRotation.x -= e.movementY * this.lookSpeed;
      this.cameraRotation.x = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, this.cameraRotation.x));
    });
  }

  _updateCamera() {
    const speed = this.keys['shift'] ? this.moveSpeed * 4 : this.moveSpeed;
    const euler = new THREE.Euler(this.cameraRotation.x, this.cameraRotation.y, 0, 'YXZ');
    this.camera.quaternion.setFromEuler(euler);

    const forward = new THREE.Vector3();
    this.camera.getWorldDirection(forward);
    const right = new THREE.Vector3()
      .crossVectors(new THREE.Vector3(forward.x, 0, forward.z).normalize(), new THREE.Vector3(0, 1, 0))
      .normalize();

    if (this.keys['w']) this.cameraPosition.addScaledVector(forward, speed);
    if (this.keys['s']) this.cameraPosition.addScaledVector(forward, -speed);
    if (this.keys['a']) this.cameraPosition.addScaledVector(right, -speed);
    if (this.keys['d']) this.cameraPosition.addScaledVector(right, speed);
    if (this.keys['q']) this.cameraPosition.y -= speed;
    if (this.keys['e']) this.cameraPosition.y += speed;
    this.camera.position.copy(this.cameraPosition);
  }

  _resize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!w || !h) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  _animate() {
    requestAnimationFrame(() => this._animate());
    this._updateCamera();
    this.renderer.render(this.scene, this.camera);
  }
}

async function main() {
  const jobId = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
  const titleEl = document.getElementById('title');
  const loadingEl = document.getElementById('loading');
  titleEl.textContent = jobId;

  const viewer = new Viewer(document.getElementById('viewer'));
  viewer.init();

  try {
    const job = await api.getJob(jobId);
    if (job.status !== 'SUCCEEDED' || !job.outputUrl) {
      loadingEl.textContent =
        job.status === 'FAILED' ? 'This job failed — no splat to view.'
                                : 'This splat is not ready yet.';
      return;
    }
    await viewer.loadSplat(job.outputUrl);
    loadingEl.style.display = 'none';
  } catch (err) {
    loadingEl.textContent = `Failed to load: ${err.message}`;
  }
}

main();

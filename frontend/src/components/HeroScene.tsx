import { useEffect, useRef, useState } from 'react';

/**
 * The scanning core behind the hero: a faceted engine with résumés in orbit.
 *
 * three.js is ~150KB gzipped, which is more than the rest of this app put together,
 * so it is loaded with a dynamic import *after* first paint. The CSS sheets render
 * immediately underneath and stay visible if this never loads — no WebGL, reduced
 * motion, or a slow connection all degrade to the same static hero rather than a hole.
 *
 * Everything allocated here is disposed on unmount: the component remounts on every
 * stage change, and GPU buffers are not garbage collected on their own.
 */
export function HeroScene() {
  const mountRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Respect the same preference the CSS animations do.
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const mount = mountRef.current;
    if (!mount) return;

    let disposed = false;
    let cleanup: (() => void) | undefined;

    void (async () => {
      let THREE: typeof import('three');
      try {
        THREE = await import('three');
      } catch {
        return; // the CSS sheets carry the hero on their own
      }
      if (disposed || !mountRef.current) return;

      const AMBER = 0xf59e0b; // amber-500, the brand accent
      const AMBER_SOFT = 0xfcd34d; // amber-300
      const STONE = 0xa8a29e; // stone-400, matching the hero's dot grid

      const width = mount.clientWidth || 900;
      const height = mount.clientHeight || 380;

      let renderer: import('three').WebGLRenderer;
      try {
        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      } catch {
        return; // no WebGL on this device
      }

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
      camera.position.set(0, 1.1, 5.6);

      renderer.setSize(width, height);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      mount.appendChild(renderer.domElement);

      scene.add(new THREE.AmbientLight(0xffffff, 1.6));
      const key = new THREE.DirectionalLight(AMBER, 2.2);
      key.position.set(5, 10, 7);
      scene.add(key);
      const fill = new THREE.PointLight(AMBER_SOFT, 12, 12);
      fill.position.set(-3, -2, 2);
      scene.add(fill);

      const core = new THREE.Group();
      scene.add(core);

      // Tracked so nothing is left on the GPU when the stage changes.
      const geometries: import('three').BufferGeometry[] = [];
      const materials: import('three').Material[] = [];

      const orbGeo = new THREE.IcosahedronGeometry(0.85, 2);
      const orbMat = new THREE.MeshPhongMaterial({
        color: AMBER,
        emissive: 0x7c4a03,
        wireframe: true,
        transparent: true,
        opacity: 0.5,
      });
      const orb = new THREE.Mesh(orbGeo, orbMat);
      core.add(orb);
      geometries.push(orbGeo);
      materials.push(orbMat);

      const innerGeo = new THREE.SphereGeometry(0.5, 24, 24);
      const innerMat = new THREE.MeshPhongMaterial({
        color: AMBER_SOFT,
        emissive: 0x92400e,
        emissiveIntensity: 0.35,
        shininess: 90,
        transparent: true,
        opacity: 0.6,
      });
      const inner = new THREE.Mesh(innerGeo, innerMat);
      core.add(inner);
      geometries.push(innerGeo);
      materials.push(innerMat);

      const ringSpecs = [
        { radius: 1.4, tube: 0.018, color: AMBER, opacity: 0.38, rx: Math.PI / 3, ry: 0 },
        {
          radius: 1.8,
          tube: 0.013,
          color: STONE,
          opacity: 0.28,
          rx: -Math.PI / 6,
          ry: Math.PI / 4,
        },
      ];
      const rings = ringSpecs.map((spec) => {
        const geo = new THREE.TorusGeometry(spec.radius, spec.tube, 16, 90);
        const mat = new THREE.MeshBasicMaterial({
          color: spec.color,
          transparent: true,
          opacity: spec.opacity,
        });
        const ring = new THREE.Mesh(geo, mat);
        ring.rotation.x = spec.rx;
        ring.rotation.y = spec.ry;
        core.add(ring);
        geometries.push(geo);
        materials.push(mat);
        return ring;
      });

      // Résumés in orbit around the engine.
      const cardGeo = new THREE.BoxGeometry(0.26, 0.36, 0.02);
      const cardMat = new THREE.MeshPhongMaterial({
        color: 0xffffff,
        specular: AMBER,
        shininess: 40,
        transparent: true,
        opacity: 0.7,
      });
      geometries.push(cardGeo);
      materials.push(cardMat);
      const cards = Array.from({ length: 6 }, (_, i) => {
        const card = new THREE.Mesh(cardGeo, cardMat);
        card.userData = { angle: (i / 6) * Math.PI * 2, distance: 1.5, y: Math.sin(i) * 0.4 };
        core.add(card);
        return card;
      });

      const dustPositions = new Float32Array(60 * 3);
      for (let i = 0; i < 60 * 3; i += 3) {
        dustPositions[i] = (Math.random() - 0.5) * 7;
        dustPositions[i + 1] = (Math.random() - 0.5) * 4.5;
        dustPositions[i + 2] = (Math.random() - 0.5) * 4;
      }
      const dustGeo = new THREE.BufferGeometry();
      dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPositions, 3));
      const dustMat = new THREE.PointsMaterial({
        color: STONE,
        size: 0.04,
        transparent: true,
        opacity: 0.4,
      });
      const dust = new THREE.Points(dustGeo, dustMat);
      scene.add(dust);
      geometries.push(dustGeo);
      materials.push(dustMat);

      let pointerX = 0;
      let pointerY = 0;
      const onPointerMove = (e: PointerEvent) => {
        pointerX = (e.clientX / window.innerWidth - 0.5) * 0.5;
        pointerY = (e.clientY / window.innerHeight - 0.5) * 0.5;
      };
      window.addEventListener('pointermove', onPointerMove, { passive: true });

      const resize = new ResizeObserver(() => {
        const w = mount.clientWidth || width;
        const h = mount.clientHeight || height;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
      });
      resize.observe(mount);

      // Pause off-screen: an invisible canvas should not hold a render loop.
      let visible = true;
      const onVisibility = () => {
        visible = document.visibilityState === 'visible';
      };
      document.addEventListener('visibilitychange', onVisibility);

      const clock = new THREE.Clock();
      let frame = 0;

      const animate = () => {
        frame = requestAnimationFrame(animate);
        if (!visible) return;

        const t = clock.getElapsedTime();

        orb.rotation.y = t * 0.28;
        orb.rotation.x = t * 0.14;
        inner.scale.setScalar(1 + Math.sin(t * 2.4) * 0.06);
        rings[0].rotation.z = t * 0.24;
        rings[1].rotation.y = -t * 0.19;

        cards.forEach((card, i) => {
          const data = card.userData as { angle: number; distance: number; y: number };
          data.angle += 0.007;
          card.position.x = Math.cos(data.angle) * data.distance;
          card.position.z = Math.sin(data.angle) * data.distance;
          card.position.y = data.y + Math.sin(t * 2 + i) * 0.12;
          card.rotation.y = -data.angle + Math.PI / 2;
          card.rotation.z = Math.sin(t + i) * 0.1;
        });

        dust.rotation.y = t * 0.035;

        core.rotation.y += (pointerX - core.rotation.y) * 0.045;
        core.rotation.x += (-pointerY - core.rotation.x) * 0.045;

        renderer.render(scene, camera);
      };
      animate();
      setReady(true);

      cleanup = () => {
        cancelAnimationFrame(frame);
        window.removeEventListener('pointermove', onPointerMove);
        document.removeEventListener('visibilitychange', onVisibility);
        resize.disconnect();
        geometries.forEach((g) => g.dispose());
        materials.forEach((m) => m.dispose());
        renderer.dispose();
        renderer.domElement.remove();
      };
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, []);

  return (
    <div
      ref={mountRef}
      className={`hero__scene${ready ? ' hero__scene--ready' : ''}`}
      aria-hidden
    />
  );
}

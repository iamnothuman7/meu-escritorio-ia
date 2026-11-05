import { Component, useEffect, useMemo, useRef, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { SCALE, CANVAS_H, CANVAS_W } from "@/features/retro-office/core/constants";
import type { FurnitureItem, RenderAgent } from "@/features/retro-office/core/types";
import { AgentModel } from "@/features/retro-office/objects/agents";
import { RemoteOfficeLayoutPreview } from "@/features/retro-office/scene/RemoteOfficeLayoutPreview";
import { createDefaultAgentAvatarProfile } from "@/lib/avatars/profile";

type Agent = { id: string | number; name: string; color: string; status: string };
type SceneOptions = { onSelect?: (id: Agent["id"]) => void; onError?: () => void };
type Point = [number, number, number];
type SceneAgent = Agent & { key: string; position: Point; avatar: RenderAgent };

const ORANGE = "#ff751f";
const INK = "#332726";
const PLASTER = "#fffcf7";
const WOOD = "#b97550";
const HOME: Point = [10, 15, 22];
const STATUS_COLORS: Record<string, string> = {
  running: "#428267", blocked: "#b83f38", queued: "#527198", review: "#785689", disabled: "#8b827a",
};
const STATIONS: Point[] = [
  [-5.1, 0, -2.55], [-1.7, 0, -2.55], [1.7, 0, -2.55], [5.1, 0, -2.55],
  [-5.1, 0, 0.9], [-1.7, 0, 0.9], [1.7, 0, 0.9], [5.1, 0, 0.9],
];

// Reuse Claw3D's layout mesh for the low partitions between work areas.
const PARTITIONS: FurnitureItem[] = [-3.4, 0, 3.4].flatMap((x, column) =>
  [-2.45, 1.0].map((z, row) => ({
    _uid: `partition-${column}-${row}`, type: "wall",
    x: (x - 0.035) / SCALE + CANVAS_W / 2,
    y: (z - 0.64) / SCALE + CANVAS_H / 2,
    w: 0.07 / SCALE, h: 1.28 / SCALE, color: "#dfae93",
  })),
);

function Box({ p, s, c, glow = false }: { p: Point; s: Point; c: string; glow?: boolean }) {
  return (
    <mesh position={p} castShadow receiveShadow>
      <boxGeometry args={s} />
      <meshStandardMaterial color={c} roughness={0.83} emissive={glow ? c : "#000"} emissiveIntensity={0.35} />
    </mesh>
  );
}

function Plant({ x, z, size = 1 }: { x: number; z: number; size?: number }) {
  return (
    <group position={[x, 0, z]} scale={size}>
      <mesh position={[0, 0.21, 0]} castShadow>
        <cylinderGeometry args={[0.3, 0.23, 0.42, 8]} />
        <meshStandardMaterial color="#c76949" />
      </mesh>
      {[0, 1, 2, 3, 4].map((leaf) => {
        const angle = leaf * 2.4;
        return (
          <mesh key={leaf} position={[Math.cos(angle) * 0.22, 0.65 + leaf * 0.08, Math.sin(angle) * 0.22]}
                rotation={[Math.sin(angle) * 0.5, angle, Math.cos(angle) * 0.5]} castShadow>
            <sphereGeometry args={[0.24, 7, 5]} />
            <meshStandardMaterial color={leaf % 2 ? "#779573" : "#426956"} />
          </mesh>
        );
      })}
    </group>
  );
}

function Desk({ point, active }: { point: Point; active: boolean }) {
  return (
    <group position={point}>
      <Box p={[0, 0.73, 0]} s={[2.3, 0.11, 1.06]} c={WOOD} />
      <Box p={[-1, 0.35, 0]} s={[0.12, 0.7, 0.87]} c="#5f514d" />
      <Box p={[1, 0.35, 0]} s={[0.12, 0.7, 0.87]} c="#5f514d" />
      <Box p={[0.07, 1.03, -0.29]} s={[0.84, 0.47, 0.06]} c={INK} />
      <Box p={[0.07, 1.03, -0.25]} s={[0.72, 0.35, 0.012]} c={active ? "#8fbca6" : "#8597ad"} glow />
      <Box p={[0.07, 0.81, -0.28]} s={[0.31, 0.05, 0.23]} c={INK} />
      <Box p={[0.02, 0.803, 0.17]} s={[0.61, 0.025, 0.17]} c="#e7dad0" />
      <Box p={[0.48, 0.807, 0.17]} s={[0.12, 0.035, 0.17]} c="#d7cbc0" />
      <Box p={[-0.82, 0.805, 0.13]} s={[0.31, 0.03, 0.28]} c="#f4d4b1" />
      <Box p={[0.95, 0.91, 0.22]} s={[0.18, 0.26, 0.18]} c={ORANGE} />
      <Box p={[0.23, 0.31, 0.94]} s={[0.52, 0.58, 0.59]} c="#6c615b" />
      <Box p={[0.23, 0.73, 1.18]} s={[0.52, 0.48, 0.13]} c="#806d62" />
    </group>
  );
}

function useLabel(text: string, background: string, color: string, dot = "", wide = false) {
  const texture = useMemo(() => {
    const canvas = document.createElement("canvas");
    canvas.width = wide ? 768 : 256;
    canvas.height = wide ? 192 : 64;
    const context = canvas.getContext("2d");
    if (!context) return null;
    const { width, height } = canvas;
    context.fillStyle = background;
    context.beginPath();
    context.roundRect(0, 0, width, height, wide ? 24 : 15);
    context.fill();
    context.font = wide ? "bold 100px system-ui" : "bold 33px system-ui";
    context.fillStyle = color;
    context.textBaseline = "middle";
    context.textAlign = "center";
    context.fillText(text, width / 2 - (dot ? 13 : 0), height / 2, width - (dot ? 63 : 20));
    if (dot) {
      context.fillStyle = dot;
      context.beginPath();
      context.arc(width - 25, height / 2, 8, 0, Math.PI * 2);
      context.fill();
    }
    const result = new THREE.CanvasTexture(canvas);
    result.colorSpace = THREE.SRGBColorSpace;
    return result;
  }, [text, background, color, dot, wide]);
  useEffect(() => () => texture?.dispose(), [texture]);
  return texture;
}

function Room({ agents }: { agents: SceneAgent[] }) {
  const brand = useLabel("NABIO OFFICE", ORANGE, "#fffaf5", "", true);
  return (
    <>
      <Box p={[0, -0.32, 0]} s={[16.5, 0.54, 11.35]} c="#ae7f65" />
      <Box p={[0, -0.065, 0]} s={[16.5, 0.13, 11.35]} c="#eddbc8" />
      {[-4, -2, 0, 2, 4].map((z) => <Box key={z} p={[0, 0.003, z]} s={[16.45, 0.006, 0.02]} c="#dbc0a8" />)}
      <Box p={[0, 1.13, -5.54]} s={[16.5, 2.26, 0.24]} c={PLASTER} />
      <Box p={[-8.14, 0.83, 0]} s={[0.24, 1.66, 11.35]} c="#fff3e5" />
      <Box p={[0, 0.15, -5.35]} s={[16.25, 0.28, 0.11]} c={ORANGE} />
      <Box p={[-7.97, 0.15, 0]} s={[0.1, 0.28, 11.03]} c={ORANGE} />
      <Box p={[0, 0.018, -0.05]} s={[13.9, 0.035, 6.35]} c="#e7c2a8" />
      <Box p={[0, 0.037, -0.05]} s={[13.7, 0.013, 6.13]} c="#f1dbc9" />
      <mesh position={[-0.05, 1.57, -5.398]}>
        <planeGeometry args={[5.55, 1.28]} />
        <meshBasicMaterial map={brand} transparent toneMapped={false} />
      </mesh>
      {[-5.83, 5.83].map((x) => (
        <group key={x} position={[x, 1.28, -5.34]}>
          <Box p={[0, 0, 0]} s={[2.76, 1.5, 0.09]} c="#776e66" />
          <Box p={[0, 0, 0.06]} s={[2.56, 1.32, 0.05]} c="#bbd1d5" />
          <Box p={[0, 0, 0.1]} s={[0.05, 1.32, 0.035]} c="#fff5e8" />
          <Box p={[0, 0, 0.1]} s={[2.56, 0.05, 0.035]} c="#fff5e8" />
        </group>
      ))}
      <RemoteOfficeLayoutPreview items={PARTITIONS} />
      {STATIONS.map((point, index) => <Desk key={index} point={point} active={agents[index]?.avatar.status === "working"} />)}
      <Box p={[-6.13, 0.23, 4.64]} s={[2.28, 0.46, 0.7]} c="#a7563d" />
      <Box p={[-6.13, 0.63, 4.87]} s={[2.28, 0.47, 0.24]} c="#d47858" />
      <Box p={[-6.13, 0.14, 3.85]} s={[1.4, 0.28, 0.59]} c="#b37a59" />
      <Box p={[6.19, 0.44, 4.55]} s={[2.45, 0.88, 1]} c="#a16c50" />
      <Box p={[6.19, 0.92, 4.55]} s={[2.59, 0.13, 1.11]} c="#f4e7d9" />
      <Box p={[6.89, 1.14, 4.57]} s={[0.55, 0.31, 0.59]} c={INK} />
      <Box p={[5.45, 1.05, 4.53]} s={[0.24, 0.15, 0.24]} c={ORANGE} />
      <Plant x={-7.25} z={-4.55} />
      <Plant x={7.25} z={-4.55} />
      <Plant x={3.75} z={4.4} size={0.7} />
    </>
  );
}

function AgentFigure({ person, selected, reducedMotion, onSelect }: {
  person: SceneAgent; selected: boolean; reducedMotion: boolean; onSelect: (id: Agent["id"]) => void;
}) {
  const avatarRef = useRef<RenderAgent[]>([person.avatar]);
  avatarRef.current[0] = person.avatar;
  const appearance = useMemo(() => {
    const profile = createDefaultAgentAvatarProfile(person.key);
    return { ...profile, clothing: { ...profile.clothing, topColor: person.color } };
  }, [person.key, person.color]);
  const statusColor = STATUS_COLORS[person.status] ?? "#aa7526";
  const label = useLabel(person.name, selected ? ORANGE : "#3b2b26", "#fffaf5", selected ? "#fffaf5" : statusColor);
  const [x, , z] = person.position;
  useFrame((_, delta) => {
    if (!reducedMotion) person.avatar.frame += Math.min(delta, 0.06) * 60;
  }, -1);
  return (
    <>
      <AgentModel agentId={person.key} name={person.name} status={person.avatar.status} color={person.color}
                  appearance={appearance} agentsRef={avatarRef} reducedMotion={reducedMotion}
                  onClick={() => onSelect(person.id)} />
      <sprite position={[x, 1.26, z]} scale={[1.28, 0.32, 1]}
              onClick={(event) => { event.stopPropagation(); onSelect(person.id); }}>
        <spriteMaterial map={label} transparent depthWrite={false} toneMapped={false} />
      </sprite>
      {selected && (
        <mesh position={[x, 0.021, z]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.31, 0.4, 32]} />
          <meshBasicMaterial color={ORANGE} transparent opacity={0.78} />
        </mesh>
      )}
    </>
  );
}

function Camera({ resetCount, reducedMotion }: { resetCount: number; reducedMotion: boolean }) {
  const { camera, gl, size, invalidate } = useThree();
  const controls = useMemo(() => new OrbitControls(camera), [camera]);
  const fit = Math.min(size.width / 20, size.height / 14.8);
  useEffect(() => {
    controls.connect(gl.domElement);
    controls.enablePan = false;
    controls.enableDamping = !reducedMotion;
    controls.minPolarAngle = Math.PI / 8;
    controls.maxPolarAngle = Math.PI / 2.4;
    controls.addEventListener("change", invalidate);
    return () => {
      controls.removeEventListener("change", invalidate);
      controls.dispose();
    };
  }, [controls, gl, invalidate, reducedMotion]);
  useEffect(() => {
    if (!(camera instanceof THREE.OrthographicCamera) || fit <= 0) return;
    controls.minZoom = fit * 0.73;
    controls.maxZoom = fit * 2.15;
    controls.target.set(0, 0.1, 0);
    camera.position.set(...HOME);
    camera.zoom = fit;
    camera.updateProjectionMatrix();
    controls.update();
    invalidate();
  }, [camera, controls, fit, invalidate, resetCount]);
  useFrame(() => controls.update());
  return null;
}

class SceneBoundary extends Component<{ children: ReactNode; onError: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.onError(); }
  render() { return this.state.failed ? null : this.props.children; }
}

function prepareAgents(agents: Agent[]): SceneAgent[] {
  return agents.slice(0, 12).map((agent, index) => {
    const point = STATIONS[index];
    const position: Point = point ? [point[0] - 0.34, 0, point[2] + 0.83] : [-2.5 + (index - 8) * 1.6, 0, 4.43];
    const key = String(agent.id);
    const color = /^#[0-9a-f]{6}$/i.test(agent.color) ? agent.color : ORANGE;
    const status = ["running", "working", "active", "executando"].includes(agent.status) ? "working"
      : ["blocked", "failed", "error", "erro"].includes(agent.status) ? "error" : "idle";
    const [x, , z] = position;
    const avatar: RenderAgent = {
      id: key, name: agent.name, color, status, item: "desk",
      x: x / SCALE + CANVAS_W / 2, y: z / SCALE + CANVAS_H / 2,
      targetX: 0, targetY: 0, path: [], facing: 0.25, frame: index * 12,
      walkSpeed: 0, phaseOffset: index, state: "standing",
    };
    return { ...agent, name: agent.name.trim().slice(0, 24), key, color, position, avatar };
  });
}

export function mountScene(container: HTMLElement, { onSelect = () => {}, onError = () => {} }: SceneOptions = {}) {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let agents: SceneAgent[] = [];
  let selectedId: Agent["id"] | null = null;
  let resetCount = 0;
  let destroyed = false;
  let failed = false;
  let visible = true;
  let canvas: HTMLCanvasElement | null = null;
  const root = createRoot(container);
  const fail = () => {
    if (destroyed || failed) return;
    failed = true;
    canvas?.removeEventListener("webglcontextlost", fail);
    queueMicrotask(() => {
      if (!destroyed) root.render(null);
    });
    onError();
  };

  function render() {
    if (destroyed || failed) return;
    root.render(
      <SceneBoundary onError={fail}>
        <Canvas orthographic camera={{ position: HOME, near: 0.1, far: 90 }}
                frameloop={document.hidden || !visible ? "never" : reducedMotion.matches ? "demand" : "always"}
                dpr={[1, 1.5]} shadows="percentage"
                gl={{ alpha: true, antialias: true, powerPreference: "low-power" }}
                onCreated={({ gl }) => {
                  canvas = gl.domElement;
                  canvas.setAttribute("aria-label", "Escritório tridimensional Nabio Office");
                  canvas.addEventListener("webglcontextlost", fail);
                }}>
          <hemisphereLight args={["#fff9e9", "#a88c7b", 2.05]} />
          <directionalLight position={[3, 10, 5]} intensity={2.2} color="#fff1db" castShadow
                            shadow-mapSize={[1024, 1024]} shadow-camera-left={-13}
                            shadow-camera-right={13} shadow-camera-top={12} shadow-camera-bottom={-12}
                            shadow-normalBias={0.04} />
          <Camera resetCount={resetCount} reducedMotion={reducedMotion.matches} />
          <Room agents={agents} />
          {agents.map((agent) => (
            <AgentFigure key={agent.key} person={agent} selected={String(selectedId) === agent.key}
                         reducedMotion={reducedMotion.matches} onSelect={onSelect} />
          ))}
        </Canvas>
      </SceneBoundary>,
    );
  }

  reducedMotion.addEventListener("change", render);
  document.addEventListener("visibilitychange", render);
  const observer = new IntersectionObserver(([entry]) => {
    if (visible !== entry.isIntersecting) {
      visible = entry.isIntersecting;
      render();
    }
  });
  observer.observe(container);
  render();
  return {
    update(nextAgents: Agent[], nextSelectedId: Agent["id"] | null = null) {
      const previous = new Map(agents.map((agent) => [agent.key, agent.avatar.frame]));
      agents = prepareAgents(nextAgents);
      for (const agent of agents) agent.avatar.frame = previous.get(agent.key) ?? agent.avatar.frame;
      selectedId = nextSelectedId;
      render();
    },
    reset() {
      resetCount++;
      render();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      canvas?.removeEventListener("webglcontextlost", fail);
      observer.disconnect();
      reducedMotion.removeEventListener("change", render);
      document.removeEventListener("visibilitychange", render);
      root.unmount();
    },
  };
}

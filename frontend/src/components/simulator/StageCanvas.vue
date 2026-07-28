<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { SimulatorView, StageLayout, StagePlacement } from "../../vite-env";
import {
  barSegmentLevel,
  beamRay,
  cablePolyline,
  css,
  placementMap,
  project,
  projectFloor,
  safeRgb,
  stageBox,
  type Box,
} from "../../lib/stage";

const props = withDefaults(defineProps<{
  layout: StageLayout | null;
  /** Non-reactive accessor: the render loop always draws the newest frame. */
  view: () => SimulatorView | null;
  showCables?: boolean;
  label?: string;
}>(), { showCables: true, label: undefined });

const host = ref<HTMLDivElement | null>(null);
const canvas = ref<HTMLCanvasElement | null>(null);
let raf = 0;
let observer: ResizeObserver | null = null;
let lastFrameAt = 0;

const CABLE = "rgba(214, 74, 52, 0.75)";

let context: CanvasRenderingContext2D | null = null;
let contextTried = false;

/** Resolved once: environments without a 2D context just render nothing. */
function acquireContext(element: HTMLCanvasElement): CanvasRenderingContext2D | null {
  if (contextTried) return context;
  contextTried = true;
  try {
    context = element.getContext("2d");
  } catch {
    context = null;
  }
  if (!context && raf) {
    window.cancelAnimationFrame(raf);
    raf = 0;
  }
  return context;
}

function fixturesForAria(): StagePlacement[] {
  return props.layout?.placements ?? [];
}

function isLightTheme(): boolean {
  return document.documentElement.dataset.theme === "light";
}

function drawBackdrop(ctx: CanvasRenderingContext2D, box: Box) {
  const light = isLightTheme();
  const backdrop = ctx.createLinearGradient(0, box.y, 0, box.y + box.height);
  if (light) {
    /* Soft peach → lavender wash so surrounding glass can refract color. */
    backdrop.addColorStop(0, "#f6eef0");
    backdrop.addColorStop(0.35, "#e8eef8");
    backdrop.addColorStop(0.72, "#dde6f4");
    backdrop.addColorStop(1, "#d0dced");
  } else {
    backdrop.addColorStop(0, "#0b0c10");
    backdrop.addColorStop(0.72, "#0e1016");
    backdrop.addColorStop(1, "#05060a");
  }
  ctx.fillStyle = backdrop;
  ctx.fillRect(box.x, box.y, box.width, box.height);

  ctx.strokeStyle = light ? "rgba(15, 23, 42, 0.08)" : "rgba(255, 255, 255, 0.05)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const y = box.y + box.height * (0.72 + i * 0.07);
    ctx.beginPath();
    ctx.moveTo(box.x + box.width * (0.06 * i), y);
    ctx.lineTo(box.x + box.width * (1 - 0.06 * i), y);
    ctx.stroke();
  }
}

function drawCables(ctx: CanvasRenderingContext2D, box: Box, layout: StageLayout) {
  const points = cablePolyline(
    box,
    layout.cable_chain,
    placementMap(layout.placements),
    layout.artnet_node,
  );
  if (points.length < 2) return;
  ctx.save();
  ctx.strokeStyle = CABLE;
  ctx.lineWidth = Math.max(1.5, box.width * 0.0022);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const next = points[i];
    const midY = (prev.y + next.y) / 2;
    ctx.bezierCurveTo(prev.x, midY, next.x, midY, next.x, next.y);
  }
  ctx.stroke();

  if (layout.artnet_node) {
    const node = project(box, layout.artnet_node.x, layout.artnet_node.y);
    const w = box.width * layout.artnet_node.width;
    const h = box.height * layout.artnet_node.height;
    ctx.fillStyle = "rgba(190, 195, 205, 0.85)";
    ctx.fillRect(node.x - w / 2, node.y - h / 2, w, h);
    ctx.fillStyle = "rgba(226, 230, 240, 0.75)";
    ctx.font = `${Math.max(9, box.width * 0.0125)}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.fillText("art-net", node.x, node.y + h);
  }
  ctx.restore();
}

function drawGlow(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  colour: { r: number; g: number; b: number },
  strength: number,
) {
  if (strength <= 0.01 || radius <= 0) return;
  const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
  gradient.addColorStop(0, css(colour, 0.55 * strength));
  gradient.addColorStop(0.45, css(colour, 0.18 * strength));
  gradient.addColorStop(1, css(colour, 0));
  ctx.fillStyle = gradient;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
}

function drawLabel(ctx: CanvasRenderingContext2D, box: Box, text: string, x: number, y: number) {
  ctx.fillStyle = isLightTheme() ? "rgba(15, 23, 42, 0.55)" : "rgba(224, 228, 238, 0.55)";
  ctx.font = `${Math.max(8, box.width * 0.0118)}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.fillText(text, x, y);
}

function drawPar(
  ctx: CanvasRenderingContext2D,
  box: Box,
  placement: StagePlacement,
  par: { r: number; g: number; b: number; w: number; intensity: number; label: string },
) {
  const point = project(box, placement.x, placement.y);
  const radius = (box.width * placement.width) / 2;
  const level = par.intensity;
  const colour = safeRgb(par);

  drawGlow(ctx, point.x, point.y, radius * (2.6 + 3.2 * level), colour, level);

  ctx.beginPath();
  ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
  ctx.fillStyle = level > 0.02 ? css(colour, 0.25 + 0.75 * level) : "rgba(60, 63, 72, 0.85)";
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.16)";
  ctx.stroke();

  drawLabel(ctx, box, par.label, point.x, point.y + radius + box.height * 0.028);
}

function drawBar(
  ctx: CanvasRenderingContext2D,
  box: Box,
  placement: StagePlacement,
  bar: {
    r: number;
    g: number;
    b: number;
    dimmer: number;
    segments: number[];
    label: string;
  },
) {
  const centre = project(box, placement.x, placement.y);
  const w = box.width * placement.width;
  const h = box.height * placement.height;
  const top = centre.y - h / 2;
  const colour = safeRgb({ ...bar, w: 0 });
  const segments = 8;
  const gap = h * 0.012;
  const segH = (h - gap * (segments - 1)) / segments;

  ctx.fillStyle = "rgba(38, 40, 47, 0.92)";
  ctx.fillRect(centre.x - w / 2, top, w, h);

  for (let i = 0; i < segments; i += 1) {
    // Segment 1 is the bottom of a vertically mounted Bar.
    const level = barSegmentLevel(bar as never, segments - 1 - i);
    const y = top + i * (segH + gap);
    if (level > 0.03) {
      drawGlow(ctx, centre.x, y + segH / 2, w * (1.6 + 5 * level), colour, level * 0.9);
    }
    ctx.fillStyle = level > 0.02 ? css(colour, 0.2 + 0.8 * level) : "rgba(54, 57, 66, 0.9)";
    ctx.fillRect(centre.x - w / 2, y, w, segH);
  }

  ctx.strokeStyle = "rgba(255, 255, 255, 0.1)";
  ctx.lineWidth = 1;
  ctx.strokeRect(centre.x - w / 2, top, w, h);
  drawLabel(ctx, box, bar.label, centre.x, top + h + box.height * 0.032);
}

function drawBeam(
  ctx: CanvasRenderingContext2D,
  box: Box,
  placement: StagePlacement,
  beam: Parameters<typeof beamRay>[2],
) {
  const { head, hit } = beamRay(box, placement, beam);
  const radius = (box.width * placement.width) / 2;
  const colour = safeRgb(beam as never);
  const level = beam.shutter_open ? beam.dimmer : 0;

  if (level > 0.02) {
    const spread = Math.max(6, box.width * 0.02 * (0.6 + beam.throw));
    const angle = Math.atan2(hit.y - head.y, hit.x - head.x);
    const nx = Math.cos(angle + Math.PI / 2) * spread;
    const ny = Math.sin(angle + Math.PI / 2) * spread;

    ctx.save();
    ctx.globalCompositeOperation = "lighter";

    const gradient = ctx.createLinearGradient(head.x, head.y, hit.x, hit.y);
    gradient.addColorStop(0, css(colour, 0.7 * level));
    gradient.addColorStop(1, css(colour, 0.08 * level));
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.moveTo(head.x, head.y);
    ctx.lineTo(hit.x + nx, hit.y + ny);
    ctx.lineTo(hit.x - nx, hit.y - ny);
    ctx.closePath();
    ctx.fill();

    // Hot core so the aim direction stays readable at low intensity.
    ctx.strokeStyle = css(colour, 0.45 * level + 0.15);
    ctx.lineWidth = Math.max(1.5, spread * 0.18);
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(head.x, head.y);
    ctx.lineTo(hit.x, hit.y);
    ctx.stroke();

    // Geometric floor hit: an ellipse, flattened by the viewing angle.
    ctx.translate(hit.x, hit.y);
    ctx.scale(1, 0.34);
    drawGlow(ctx, 0, 0, spread * 2.4, colour, level);
    ctx.restore();
  }

  // Fixed body at the top of the stage, yoke rotated by pan.
  ctx.save();
  ctx.translate(head.x, head.y);
  ctx.rotate((beam.pan - 0.5) * 0.9);
  ctx.fillStyle = "rgba(48, 51, 60, 0.95)";
  ctx.fillRect(-radius, -radius * 0.9, radius * 2, radius * 1.8);
  ctx.strokeStyle = "rgba(255, 255, 255, 0.18)";
  ctx.lineWidth = 1;
  ctx.strokeRect(-radius, -radius * 0.9, radius * 2, radius * 1.8);
  ctx.restore();

  // Ceiling-mounted rig: a small base plate above the head reads as a
  // hanging fixture (base up, head down) instead of a floor/truss unit.
  if (placement.mount === "ceiling") {
    const baseW = radius * 2.2;
    const baseH = radius * 0.6;
    ctx.fillStyle = "rgba(70, 74, 84, 0.95)";
    ctx.fillRect(head.x - baseW / 2, head.y - radius - baseH, baseW, baseH);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.18)";
    ctx.lineWidth = 1;
    ctx.strokeRect(head.x - baseW / 2, head.y - radius - baseH, baseW, baseH);
  }

  ctx.beginPath();
  ctx.arc(head.x, head.y, radius * 0.55, 0, Math.PI * 2);
  ctx.fillStyle = level > 0.02 ? css(colour, 0.35 + 0.65 * level) : "rgba(70, 74, 84, 0.9)";
  ctx.fill();

  drawLabel(ctx, box, beam.label, head.x, head.y - radius - box.height * 0.012);
}

function render() {
  raf = window.requestAnimationFrame(render);
  const element = canvas.value;
  const layout = props.layout;
  if (!element || !layout) return;
  const ctx = acquireContext(element);
  if (!ctx) return;

  const now = performance.now();
  // Cap at ~60 fps; the newest frame always wins, stale frames are dropped.
  if (now - lastFrameAt < 15) return;
  lastFrameAt = now;

  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const width = element.clientWidth;
  const height = element.clientHeight;
  if (width === 0 || height === 0) return;
  if (element.width !== Math.round(width * dpr) || element.height !== Math.round(height * dpr)) {
    element.width = Math.round(width * dpr);
    element.height = Math.round(height * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const box = stageBox(width, height);
  drawBackdrop(ctx, box);

  const view = props.view();
  const placements = placementMap(layout.placements);

  if (props.showCables !== false) drawCables(ctx, box, layout);

  // Bodies first, then beams in additive mode: light must never be occluded
  // by the fixtures it flies past.
  for (const bar of view?.bars ?? []) {
    const placement = placements[bar.id];
    if (placement) drawBar(ctx, box, placement, bar);
  }
  for (const par of [...(view?.pars ?? []), ...(view?.faces ?? [])]) {
    const placement = placements[par.id];
    if (placement) drawPar(ctx, box, placement, par);
  }
  for (const beam of view?.beams ?? []) {
    const placement = placements[beam.id];
    if (placement) drawBeam(ctx, box, placement, beam);
  }

  if (!view) {
    for (const placement of layout.placements ?? []) {
      const point =
        placement.orientation === "vertical"
          ? project(box, placement.x, placement.y)
          : project(box, placement.x, placement.y);
      ctx.fillStyle = "rgba(90, 94, 104, 0.8)";
      ctx.beginPath();
      ctx.arc(point.x, point.y, box.width * 0.008, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Floor reference line so the geometry reads as a stage, not a card grid.
  const floorLeft = projectFloor(box, 0.02, 0.9);
  const floorRight = projectFloor(box, 0.98, 0.9);
  ctx.strokeStyle = "rgba(255, 255, 255, 0.07)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(floorLeft.x, floorLeft.y);
  ctx.lineTo(floorRight.x, floorRight.y);
  ctx.stroke();
}

onMounted(() => {
  if (typeof ResizeObserver !== "undefined" && host.value) {
    observer = new ResizeObserver(() => {
      lastFrameAt = 0;
    });
    observer.observe(host.value);
  }
  if (typeof window !== "undefined" && window.requestAnimationFrame) {
    raf = window.requestAnimationFrame(render);
  }
});

onBeforeUnmount(() => {
  if (raf) window.cancelAnimationFrame(raf);
  observer?.disconnect();
});

watch(
  () => props.layout,
  () => {
    lastFrameAt = 0;
  },
);
</script>

<template>
  <div
    ref="host"
    class="stage-canvas"
    :data-cables="showCables !== false"
  >
    <canvas
      ref="canvas"
      class="stage-canvas__surface"
      :aria-label="label ?? 'Сцена: симулятор світла'"
      role="img"
    />
    <ul class="visually-hidden">
      <li
        v-for="placement in fixturesForAria()"
        :key="placement.fixture_id"
        :data-id="placement.fixture_id"
        :data-kind="placement.kind"
        :data-orientation="placement.orientation"
      >
        {{ placement.label ?? placement.fixture_id }}
      </li>
    </ul>
  </div>
</template>

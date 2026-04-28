// Patched x6-html-shape: replaces View.createElement (removed in X6 3.x) with document.createElement
import { Node as p, NodeView as l, Graph as C, Dom as s } from "@antv/x6";
import { getConfig as w, clickable as x, isInputElement as y, forwardEvent as S } from "./utils.js";

const u = "html-shape", h = "html-shape-view", T = p.define(w(h)), m = {};

export function register(i) {
  const { shape: e, render: n, inherit: t = u, ...o } = i;
  if (!e) throw new Error("should specify shape in config");
  m[e] = n;
  C.registerNode(e, { inherit: t, ...o }, true);
}

const a = "html";

// Determine which HTML layer a node belongs to.
// Parent (loop/iteration) nodes go behind the SVG layer so edges render above them.
// All other nodes go in front of the SVG layer so they render above edges.
function isBackNode(cell) {
  const type = cell.getData?.()?.type;
  return type === 'loop' || type === 'iteration';
}

// Ensure the two HTML container layers exist and are correctly positioned.
function ensureHtmlLayers(graph) {
  if (!graph._htmlBack) {
    const back = graph._htmlBack = document.createElement('div');
    s.css(back, {
      position: 'absolute', width: '100%', height: '100%',
      'touch-action': 'none', 'user-select': 'none', 'pointer-events': 'none',
      'z-index': 0, 'transform-origin': 'left top',
    });
    back.classList.add('x6-html-shape-container', 'x6-html-shape-back');
    const svg = graph.container.querySelector('svg');
    // back layer: before SVG → visually behind edges
    graph.container.insertBefore(back, svg || null);
  }
  if (!graph._htmlFront) {
    const front = graph._htmlFront = document.createElement('div');
    s.css(front, {
      position: 'absolute', width: '100%', height: '100%',
      'touch-action': 'none', 'user-select': 'none', 'pointer-events': 'none',
      'z-index': 0, 'transform-origin': 'left top',
    });
    front.classList.add('x6-html-shape-container', 'x6-html-shape-front');
    // front layer: after SVG → visually above edges
    graph.container.append(front);
  }
  // Keep legacy alias so updateHtmlContainerSize can iterate both
  graph.htmlContainers = [graph._htmlBack, graph._htmlFront];
}

class BaseHTMLShapeView extends l {
  confirmUpdate(e) {
    const n = super.confirmUpdate(e);
    return this.handleAction(n, a, () => {
      if (!this.mounted) {
        const t = m[this.cell.shape], o = this.ensureComponentContainer();
        t && o && (this.mounted = t(this.cell, this.graph, o) || true,
          this.onMounted(),
          o.addEventListener("mousedown", this.prevEvent, true),
          o.addEventListener("mouseup", this.prevEvent, true));
      }
    });
  }
  prevEvent(e) {
    (x(e.target) || y(e.target)) && (e.preventDefault(), e.stopPropagation());
  }
  ensureComponentContainer() {}
  onMounted() {}
  onUnMount() {
    if (this.onZIndexChange) {
      this.cell.off("change:zIndex", this.onZIndexChange);
    }
    if (this.onNodeMoving) {
      this.graph.off("node:moving", this.onNodeMoving);
    }
  }
  unmount() {
    typeof this.mounted == "function" && this.mounted();
    this.componentContainer && this.componentContainer.remove();
    this.onUnMount();
    return super.unmount(), this;
  }
}

BaseHTMLShapeView.config({ bootstrap: [a], actions: { component: a } });

class HTMLShapeView extends BaseHTMLShapeView {
  constructor(...e) {
    super(...e);
    this.cell.on("change:visible", ({ cell: n }) => {
      if (n.view === h) {
        const t = this.graph.findViewByCell(n.id);
        t && Promise.resolve().then(() => {
          t.componentContainer.style.display = t.container.style.display;
        });
      }
    });
  }
  onMounted() {
    const listeners = this.graph.listeners;
    // Always register per-cell zIndex listener regardless of shared transform events
    this.onZIndexChange = () => this.updateContainerStyle();
    this.cell.on("change:zIndex", this.onZIndexChange);
    if (listeners?.hasTransformEvent?.length) return;
    this.onTranslate = this.updateHtmlContainerSize.bind(this);
    this.graph.on("translate", this.onTranslate);
    this.graph.on("scale", this.onTranslate);
    this.graph.on("node:change:position", this.onTranslate);
    this.graph.on("hasTransformEvent", this.onTranslate);
    // While dragging, lift this node's componentContainer to the top of its
    // layer so its ports are never obscured by a sibling node underneath.
    this.onNodeMoving = ({ node }) => {
      if (node === this.cell && this.componentContainer) {
        const layer = isBackNode(this.cell) ? this.graph._htmlBack : this.graph._htmlFront;
        layer.append(this.componentContainer);
      }
    };
    this.graph.on("node:moving", this.onNodeMoving);
    this.updateHtmlContainerSize();
  }
  ensureComponentContainer() {
    ensureHtmlLayers(this.graph);
    const layer = isBackNode(this.cell) ? this.graph._htmlBack : this.graph._htmlFront;
    if (!this.componentContainer) {
      const e = this.componentContainer = document.createElement("div");
      s.css(e, {
        "pointer-events": "auto", "touch-action": "none", "user-select": "none",
        "transform-origin": "center", position: "absolute"
      });
      e.classList.add("x6-html-shape-node");
      "click,dblclick,contextmenu,mousedown,mousemove,mouseup,mouseover,mouseout,mouseenter,mouseleave"
        .split(",").forEach(t => S(t, e, this.container));
      layer.append(e);
    }
    return this.componentContainer;
  }
  resize() { super.resize(); this.updateContainerStyle(); }
  updateTransform() { super.updateTransform(); this.updateContainerStyle(); }
  updateContainerStyle() {
    const e = this.ensureComponentContainer();
    const { x: n, y: t } = this.cell.getBBox();
    const { width: o, height: r } = this.cell.getSize();
    const g = getComputedStyle(this.container).cursor;
    const f = this.cell.getZIndex() ?? 0;
    // Shrink the interactive width by the port hover radius (6px) so the right
    // port circle is fully outside the componentContainer and never blocked by it.
    // overflow:visible keeps the visual rendering intact.
    const PORT_RADIUS = 6;
    s.css(e, {
      cursor: g, height: r + "px", width: (o - PORT_RADIUS) + "px",
      overflow: "visible",
      "z-index": f,
      transform: `translate(${n}px, ${t}px) rotate(${this.cell.getAngle()}deg)`
    });
  }
  updateHtmlContainerSize() {
    const { graph: e } = this;
    const t = e.transform.getMatrix();
    const { offsetHeight: o, offsetWidth: r } = e.container;
    const n = e.transform.getZoom();
    const style = {
      transform: `matrix(${t.a}, ${t.b}, ${t.c}, ${t.d}, ${t.e}, ${t.f})`,
      width: r / n + "px",
      height: o / n + "px",
    };
    // Update both layers
    (e.htmlContainers || [e._htmlBack, e._htmlFront].filter(Boolean)).forEach(c => s.css(c, style));
  }
}

l.registry.register(h, HTMLShapeView, true);
p.registry.register(u, T, true);

export { BaseHTMLShapeView, T as HTMLShape, u as HTMLShapeName, HTMLShapeView, h as HTMLView, a as action };

/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-06 11:54:29 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-06 11:54:29 
 */
import { Dom as u, ObjectExt as l, Markup as c } from "@antv/x6";
const o = "fo-shape-view";
function p(t, e, r) {
  e.addEventListener(t, function(n) {
    r.dispatchEvent(new n.constructor(n.type, n)), n.preventDefault(), n.stopPropagation();
  });
}
function s(t, e = 3) {
  return !t || !u.isHTMLElement(t) || e <= 0 ? !1 : ["a", "button"].includes(u.tagName(t)) || t.getAttribute("role") === "button" || t.getAttribute("type") === "button" ? !0 : s(t.parentNode, e - 1);
}
function g(t) {
  if (u.tagName(t) === "input") {
    const r = t.getAttribute("type");
    if (r == null || ["text", "password", "number", "email", "search", "tel", "url"].includes(
      r
    ))
      return !0;
  }
  return !1;
}
function f(t = "rect", e = !0) {
  return [
    {
      tagName: t,
      selector: "body"
    },
    e ? c.getForeignObjectMarkup() : null,
    {
      tagName: "text",
      selector: "label"
    }
  ].filter((r) => r);
}
function b(t) {
  return {
    view: t,
    markup: f("rect", t === o),
    attrs: {
      body: {
        // fill: "none",
        // 这里很奇怪，none的时候不能触发节点移动，改成transparent可以触发
        fill: "transparent",
        stroke: "none",
        refWidth: "100%",
        refHeight: "100%"
      },
      label: {
        fontSize: 14,
        fill: "#333",
        refX: "50%",
        refY: "50%",
        textAnchor: "middle",
        textVerticalAnchor: "middle"
      },
      fo: {
        refWidth: "100%",
        refHeight: "100%"
      }
    },
    propHooks(e) {
      if (e.markup == null) {
        const { primer: r, view: n } = e;
        if (r && r !== "rect") {
          e.markup = f(r, n === o);
          let i = {};
          r === "circle" ? i = {
            refCx: "50%",
            refCy: "50%",
            refR: "50%"
          } : r === "ellipse" && (i = {
            refCx: "50%",
            refCy: "50%",
            refRx: "50%",
            refRy: "50%"
          }), e.attrs = l.merge(
            {},
            {
              body: {
                refWidth: null,
                refHeight: null,
                ...i
              }
            },
            e.attrs || {}
          );
        }
      }
      return e;
    }
  };
}
export {
  o as FOView,
  s as clickable,
  p as forwardEvent,
  b as getConfig,
  g as isInputElement
};

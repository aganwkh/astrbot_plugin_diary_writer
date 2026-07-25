const SVG_NS = "http://www.w3.org/2000/svg";

function svgNode(name, attributes = {}) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

export function lineChart(points, valueKey, label) {
  const values = points.map((point) => Number(point[valueKey])).filter(Number.isFinite);
  if (!values.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "暂无可显示的数据。";
    return empty;
  }
  const width = 640;
  const height = 180;
  const padding = 18;
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || 1;
  const chart = svgNode("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label });
  chart.append(svgNode("line", { x1: padding, y1: height - padding, x2: width - padding, y2: height - padding, class: "chart-axis" }));
  const path = points.map((point, index) => {
    const value = Number(point[valueKey]);
    const x = padding + (index * (width - padding * 2)) / Math.max(points.length - 1, 1);
    const y = height - padding - ((value - low) * (height - padding * 2)) / span;
    return `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
  chart.append(svgNode("path", { d: path, class: "chart-line" }));
  return chart;
}

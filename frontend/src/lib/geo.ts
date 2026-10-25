// d3-geo treats polygon rings on the sphere: an exterior ring must wind
// clockwise (the opposite of RFC 7946), otherwise d3 draws the complement
// (the whole globe minus the polygon). Rewind for rendering only.

function ringArea(r: number[][]): number {
  let a = 0;
  for (let i = 0, n = r.length; i < n; i++) {
    const [x1, y1] = r[i];
    const [x2, y2] = r[(i + 1) % n];
    a += x1 * y2 - x2 * y1;
  }
  return a / 2;
}

function fixRings(rings: number[][][]): number[][][] {
  return rings.map((r, i) => {
    const a = ringArea(r);
    const wantNegative = i === 0; // exterior clockwise for d3, holes counter-clockwise
    return (wantNegative ? a > 0 : a < 0) ? [...r].reverse() : r;
  });
}

export function rewindForD3(g: any): any {
  if (!g) return g;
  if (g.type === "Feature") return { ...g, geometry: rewindForD3(g.geometry) };
  if (g.type === "FeatureCollection") return { ...g, features: g.features.map(rewindForD3) };
  if (g.type === "Polygon") return { ...g, coordinates: fixRings(g.coordinates) };
  if (g.type === "MultiPolygon") return { ...g, coordinates: g.coordinates.map(fixRings) };
  return g;
}

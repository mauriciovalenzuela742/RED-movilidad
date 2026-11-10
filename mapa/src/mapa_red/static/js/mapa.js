// Mapa base (MapLibre), capa de deck.gl, encuadres y ubicación de la barra de reproducción.
import { tooltip } from "./capas.js";
import { VISTA_INICIAL } from "./dominio.js";
import { datos, tray, vista } from "./estado.js";
import { $, DURACION_MAPA } from "./util.js";

const ESTILO_MAPA = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";  // mapa base claro, como red.cl

// onClick y onHover reciben el objeto de deck.gl bajo el cursor (o nada)
export function crearMapa({ onClick, onHover }) {
  vista.mapa = new maplibregl.Map({ container: "mapa", style: ESTILO_MAPA, ...VISTA_INICIAL, pitch: 0 });
  vista.mapa.addControl(new maplibregl.NavigationControl(), "top-right");
  vista.overlay = new deck.MapboxOverlay({
    interleaved: false, layers: [], getTooltip: tooltip,
    onClick: ({ object }) => onClick(object), onHover: ({ object }) => onHover(object),
  });
  vista.mapa.addControl(vista.overlay);
}

// Parte libre del mapa (márgenes en píxeles), según los paneles visibles (plegado, ranking cerrado):
// lo encuadrado queda entre el panel de la izquierda y el ranking de la derecha
function zonaLibre() {
  const W = innerWidth, m = 24, p = { top: m, bottom: m, left: m, right: m };
  for (const id of ["panel", "panelRanking"]) {
    const r = $(id).getBoundingClientRect();
    if (!r.width || !r.height) continue;  // oculto
    if (r.left < W / 2) p.left = Math.max(p.left, r.right + m);
    else p.right = Math.max(p.right, W - r.left + m);
  }
  // que siempre quede al menos un tercio del ancho
  const lados = p.left + p.right, max = W * 2 / 3;
  if (lados > max) { p.left *= max / lados; p.right *= max / lados; }
  return p;
}

// La barra de reproducción ocupa el ancho libre al pie del mapa; el encuadre deja espacio para ella
export function ubicarReproductor() {
  const p = zonaLibre(), barra = $("reproductor");
  barra.style.left = p.left + "px";
  barra.style.right = p.right + "px";
}

function encuadrarPuntos(puntos, maxZoom) {
  if (!puntos.length) return;
  let x0 = 180, x1 = -180, y0 = 90, y1 = -90;
  for (const [x, y] of puntos) {
    x0 = Math.min(x0, x); x1 = Math.max(x1, x);
    y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  const padding = zonaLibre(), barra = $("reproductor");
  if (!barra.hidden) padding.bottom += barra.offsetHeight + 24;
  vista.mapa.fitBounds([[x0, y0], [x1, y1]], { padding, maxZoom, duration: DURACION_MAPA });
}

export const encuadrarCeldas = () => encuadrarPuntos(datos.ultimasCeldas.map(d => d.pos), 14);
export const encuadrarTrayecto = () => encuadrarPuntos(tray.pts.map(p => p.pos), 15);
export const volverAVistaInicial = () => vista.mapa.flyTo({ ...VISTA_INICIAL, duration: DURACION_MAPA });

// Capas de deck.gl de cada vista (calor, recorridos, trayecto) y sus tooltips.
import { COLOR_OTROS, COLOR_SIN_DATOS, METRICAS, PALETA, RAMPA, colorPara } from "./colores.js";
import { METROS_POR_GRADO } from "./dominio.js";
import { datos, estado, tipicas, tray } from "./estado.js";
import { usaTipicas } from "./tipicas.js";
import { capasTrayecto, colorActual, colorRecorrido } from "./trayecto.js";
import { $, esc, fmt } from "./util.js";

const ESTILO_TOOLTIP = { background: "#FFFFFF", color: "#000000", fontSize: "12px", lineHeight: "16px",
                         padding: "12px 16px", border: "none", borderTop: "3px solid #CF152D", borderRadius: "12px",
                         boxShadow: "0 1px 2px rgba(0,0,0,.30), 0 2px 6px 2px rgba(0,0,0,.15)",
                         fontFamily: "'Open Sans', Roboto, sans-serif" };
const GRIS = "#58595B";

// Color del recorrido i de la vista Recorridos (el servidor manda los 5 con más registros, uno por color de
// la paleta, y el resto como "Otros", en gris)
export const colorRec = i =>
  datos.porRec?.recorridos[i]?.id === "Otros" || i >= PALETA.length ? COLOR_OTROS : PALETA[i];

export function capas(celdas) {
  if (estado.modo === "trayecto") return capasTrayecto();
  if (estado.modo === "recorrido" && datos.porRec) return capasRecorridos();
  return capasCalor(celdas);
}

function capasRecorridos() {
  const { porRec } = datos;
  const celdas = porRec.celdas.filter(c => c.n >= estado.minObs);
  const idx = id => porRec.recorridos.findIndex(r => r.id === id);
  // de menos a más registros: donde comparten calles, el recorrido principal queda arriba
  const lineas = [...(tipicas.datos || [])].sort((a, b) => idx(b.recorrido) - idx(a.recorrido));
  return [
    new deck.ScatterplotLayer({
      id: "recorridos", data: celdas, getPosition: c => [c.x, c.y],
      // con trayectorias, las celdas quedan suaves de fondo
      getFillColor: c => [...colorRec(c.i), lineas.length ? 70 : 210], getRadius: 42, radiusUnits: "meters",
      radiusMinPixels: 2.5, pickable: true, autoHighlight: true, highlightColor: [0, 0, 0, 140],
      updateTriggers: { getFillColor: [porRec, lineas.length] },
    }),
    // Trayectoria típica de cada recorrido (un viaje real, unido): borde blanco + línea de su color
    new deck.PathLayer({
      id: "tipicas-borde", data: lineas, getPath: d => d.camino, getColor: [255, 255, 255, 230],
      getWidth: 7, widthUnits: "pixels", jointRounded: true, capRounded: true,
    }),
    new deck.PathLayer({
      id: "tipicas", data: lineas, getPath: d => d.camino,
      getColor: d => [...colorRec(Math.max(0, idx(d.recorrido))), 255],
      getWidth: 4, widthUnits: "pixels", jointRounded: true, capRounded: true,
      pickable: true, autoHighlight: true, highlightColor: [0, 0, 0, 120],
      updateTriggers: { getColor: [porRec] },
    }),
  ];
}

function capasCalor(celdas) {
  const m = METRICAS[estado.metrica];
  const conLinea = usaTipicas() && !!tipicas.datos?.length;
  return [
    // aggregation MEAN: el color refleja el promedio de la métrica, no la densidad de puntos
    new deck.HeatmapLayer({
      id: "calor", data: celdas, getPosition: d => d.pos, getWeight: d => d.valor,
      aggregation: "MEAN", radiusPixels: estado.radio, intensity: 1, threshold: 0.03,
      colorDomain: m.dominio, colorRange: m.invertir ? [...RAMPA].reverse() : RAMPA,
      opacity: conLinea ? 0.3 : 1,  // con la trayectoria, el calor queda de fondo
      updateTriggers: { getWeight: [estado.metrica] },
    }),
    ...(conLinea ? capasLineaVelocidad(celdas) : []),
    // puntos invisibles para el tooltip (el HeatmapLayer no se puede seleccionar)
    new deck.ScatterplotLayer({
      id: "calor-tooltip", data: celdas, getPosition: d => d.pos, getRadius: CONFIG.tam * METROS_POR_GRADO * 0.5,
      radiusUnits: "meters", getFillColor: [0, 0, 0, 0], pickable: true,
    }),
  ];
}

// En Calor, con un servicio o recorrido filtrado: su trayectoria típica encima del calor, cada tramo del
// color de la celda por la que pasa según la métrica, el tipo de día y la hora. Se ve el camino y, sobre
// él, dónde va lento o rápido (cambia con la animación por hora).
function capasLineaVelocidad(celdas) {
  const t = CONFIG.tam, clave = p => `${Math.floor(p[0] / t)}_${Math.floor(p[1] / t)}`;
  const porCelda = new Map(celdas.map(d => [clave(d.pos), d]));
  const tramos = [];
  for (const tip of tipicas.datos) {
    for (let k = 1; k < tip.camino.length; k++) {
      const a = tip.camino[k - 1], b = tip.camino[k], d = porCelda.get(clave(b)) || porCelda.get(clave(a));
      tramos.push({ tipo: "tramoVel", a, b, recorrido: tip.recorrido, d });
    }
  }
  return [
    new deck.PathLayer({ id: "linea-vel-borde", data: tipicas.datos, getPath: x => x.camino,
      getColor: [255, 255, 255, 235], getWidth: 10, widthUnits: "pixels", jointRounded: true, capRounded: true }),
    new deck.LineLayer({
      id: "linea-vel", data: tramos, getSourcePosition: s => s.a, getTargetPosition: s => s.b,
      // sin celda (pocos registros a esa hora) → gris
      getColor: s => [...(s.d ? colorPara(s.d.valor, estado.metrica) : COLOR_SIN_DATOS), 255],
      getWidth: 6, widthUnits: "pixels", pickable: true, autoHighlight: true, highlightColor: [0, 0, 0, 120],
    }),
  ];
}

const html = cuerpo => ({ html: cuerpo, style: ESTILO_TOOLTIP });

// Muestra de color junto a un texto (el texto va en tinta, nunca en el color del dato)
const muestra = c => `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;` +
  `vertical-align:-1px;background:rgb(${c})"></span>`;

export function tooltip({ object: d }) {
  if (!d) return null;
  if (d.tipo === "punto" || d.tipo === "tramo" || d.tipo === "camino") {  // trayecto: punto GPS o línea
    const porVelocidad = colorActual() === "velocidad";
    const enc = (tray.varios ? `<b>Bus ${esc(d.patente)}</b><br>` : "") +
      `${porVelocidad ? "" : muestra(colorRecorrido(d.recorrido))}${tray.varios ? "" : "<b>"}${esc(d.recorrido)}${tray.varios ? "" : "</b>"}<br>`;
    const pie = tray.varios ? `<br><span style="color:${GRIS}">Clic para ver solo este bus</span>` : "";
    if (d.tipo === "camino")
      return html(enc + `<b>Viaje:</b> ${esc(d.inicio)}–${esc(d.fin)}` + pie);
    const vel = `<b>Velocidad:</b> ${porVelocidad ? muestra(colorPara(d.v ?? 0, "vel_mov")) : ""}` +
      `${d.v == null ? "–" : fmt(d.v)} km/h${d.tipo === "tramo" ? " (promedio del tramo)" : ""}`;
    if (d.tipo === "tramo")
      return html(enc + `<b>Hora:</b> ${esc(d.inicio)}–${esc(d.fin)}<br>${vel}` + pie);
    return html(enc + `<b>Hora:</b> ${esc(d.hora)}<br>${vel}<br>
      <span style="color:${GRIS}">${fmt(d.pos[1], 5)}, ${fmt(d.pos[0], 5)}</span>` + pie);
  }
  if (d.tipo === "tramoVel") {  // tramo de la trayectoria coloreada por velocidad (Calor)
    const m = METRICAS[estado.metrica];
    return html(`<b>${esc(d.recorrido)}</b><br>` + (d.d
      ? `${esc($("metrica").selectedOptions[0].text)}: <b>${fmt(d.d.valor)} ${m.titulo}</b><br>Registros en la celda: ${fmt(d.d.n, 0)}`
      : "Sin datos suficientes en esta celda para el tipo de día y la hora elegidos"));
  }
  if (d.camino) {  // trayectoria típica de un recorrido
    return html(`<b>Trayectoria típica de ${esc(d.recorrido)}</b><br>
      Viaje real del bus ${esc(d.patente)}, ${esc(d.dia)}, ${esc(d.inicio)}–${esc(d.fin)}<br>${fmt(d.n, 0)} puntos GPS`);
  }
  if (d.i != null) {  // celda de la vista por recorrido: i = recorrido, x/y = lon/lat, v = km/h, d = % det.
    const r = datos.porRec.recorridos[d.i];
    return html(`${muestra(colorRec(d.i))}<b>${esc(r.id)}</b><br>
      <b>Vel. en movimiento:</b> ${fmt(d.v)} km/h<br>
      <b>Detenido:</b> ${fmt(d.d, 0)} %<br>
      <b>Registros:</b> ${fmt(d.n, 0)}`);
  }
  return html(`<b>Vel. en movimiento:</b> ${fmt(d.vel_mov)} km/h<br>
    <b>Vel. efectiva:</b> ${fmt(d.vel_efec)} km/h<br>
    ${d.vel_com != null ? `<b>Vel. comercial en ruta:</b> ${fmt(d.vel_com)} km/h<br>` : ""}
    <b>Detenido:</b> ${fmt(d.pct_det, 0)} %<br>
    <b>Registros:</b> ${fmt(d.n, 0)}<br>
    <span style="color:${GRIS}">${fmt(d.pos[1], 4)}, ${fmt(d.pos[0], 4)}</span>`);
}

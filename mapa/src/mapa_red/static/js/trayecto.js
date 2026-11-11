// Trayecto: puntos GPS de un bus (o de todos los de lo filtrado) en un día operativo, sus capas y la
// reproducción en el navegador (sin volver a pedir datos).
//
// Rendimiento: todo lo que se dibuja se arma una vez al cargar (caminos por viaje, tramos entre puntos
// seguidos, puntos) y las capas reciben siempre los mismos arreglos y las mismas funciones. En cada cuadro
// solo cambian la hora de las TripsLayer (`currentTime`) y el rango del filtro de tiempo de los puntos
// (DataFilterExtension): deck.gl los pasa a la GPU sin recalcular nada. Antes cada cuadro volvía a filtrar
// todos los puntos y deck.gl rehacía las capas; con ~67 000 puntos bloqueaba el hilo principal por
// segundos y los clics (ej. "Pausar") se atendían tarde.
import { iconoPlay } from "./avisos.js";
import { COLOR_BORDE, COLOR_OTROS, colorPara, coloresPorRecorrido } from "./colores.js";
import { fueraDeServicio, textoValor } from "./dominio.js";
import { conReferencia } from "./referencia.js";
import { estado, tray, vista } from "./estado.js";
import { $, consulta, fmt, hhmm, pedirJson } from "./util.js";

const ESTELA = 20 * 60;      // s: en la reproducción, lo recorrido en los últimos 20 min va más marcado
const SENAL_RECIENTE = 300;  // s: un bus en marcha se marca solo si tuvo señal en los últimos 5 min
// s: el GPS registra cada 30 s; con más de 3 registros perdidos entre dos puntos no se unen con una recta
// (cruzaría manzanas: no se sabe por dónde fue) y el bus en marcha espera en su último punto
const HUECO_LINEA = 90;
const SIN_LIMITE = 1e9;      // hora "infinita": sin reproducción se ve el día completo
const filtroTiempo = new deck.DataFilterExtension({ filterSize: 1 });  // una sola instancia: si cambia, deck.gl rehace la capa

// Accesores estables (las mismas funciones en cada cuadro)
const camino = d => d.path, tiempos = d => d.ts, posicion = p => p.pos, horaDe = p => p.t;

// ── Colores: por velocidad (la métrica de la app) o por recorrido. Nunca por bus: con decenas de buses los
// colores se repetirían; cada bus se identifica por su patente, el tooltip y el resaltado al pasar el mouse.
export const colorActual = () => tray.colorPor ?? (tray.varios ? "velocidad" : "recorrido");
export const colorRecorrido = rec => tray.colores.get(rec) || COLOR_OTROS;
const colorDe = d => colorActual() === "velocidad" ? colorPara(d.v ?? 0, "vel_mov") : colorRecorrido(d.recorrido);

export const puedeTrayecto = () => !!(estado.filtros.patente || estado.filtros.servicio || estado.filtros.recorrido);
// Nombre de lo filtrado para los textos ("T301 00R", "T301 ida"); vacío sin filtros de recorrido
export const nombreFiltro = () => estado.filtros.recorrido || [estado.filtros.servicio,
  estado.filtros.sentido && textoValor("sentido", estado.filtros.sentido).toLowerCase()].filter(Boolean).join(" ");

// Días con datos (vienen en /vista, o de /dias al entrar al modo sin bus). Con bus y filtro de servicio /
// sentido / recorrido, solo los días en que el bus hizo eso; sin bus, los días en que alguien hizo lo
// filtrado. Se mantiene el día elegido si sigue en la lista; si no, el de más puntos.
export async function cargarDias() {
  ponerDias(await pedirJson("/dias" + consulta(estado.filtros)));
  await cargarTrayecto();
}

export function ponerDias(dias) {
  tray.dias = dias;
  if (!tray.dias.some(d => d.dia === tray.dia))
    tray.dia = tray.dias.reduce((a, b) => (b.n_filtro > a.n_filtro ? b : a), tray.dias[0] || {}).dia || null;
  $("trayDia").innerHTML = tray.dias.length ? tray.dias.map(d =>
    `<option value="${d.dia}" data-sec="${d.n_filtro < d.n ? `${fmt(d.n_filtro, 0)} de ${fmt(d.n, 0)}` : fmt(d.n, 0)} puntos">${d.dia}</option>`).join("")
    : `<option value="">Ningún día con estos filtros</option>`;
  $("trayDia").value = tray.dia || "";
}

// `vigente`: si al llegar la respuesta ya no lo es (los filtros cambiaron entretanto), no se usa
export async function cargarTrayecto(vigente = () => true) {
  pararAnimacion(); tray.tiempo = null; tray.resaltado = null;
  const varios = !estado.filtros.patente;
  if (!tray.dia) { tray.varios = varios; preparar([]); return; }
  const filas = await pedirJson((varios ? "/trayectos" : "/trayecto") + consulta(estado.filtros, { dia: tray.dia }));
  if (!vigente()) return;
  tray.varios = varios;  // junto con los puntos: mientras llegan, sigue lo anterior completo
  preparar(filas);
}

export function vaciarTrayecto() {
  pararAnimacion(); tray.tiempo = null; tray.resaltado = null; preparar([]);
}

const contar = (pts, clave) => {
  const n = new Map();
  for (const p of pts) n.set(p[clave], (n.get(p[clave]) || 0) + 1);
  return n;
};
const agrupar = (xs, clave) => {
  const g = new Map();
  for (const x of xs) (g.get(x[clave]) ?? g.set(x[clave], []).get(x[clave])).push(x);
  return g;
};

// Arma una vez todo lo que dibujan las capas. Un viaje = un bus en un recorrido, sin cortes de señal largos
// ni saltos de GPS (lo calcula el servidor). Se dibuja como caminos de un color (cortados en los huecos de
// señal) o como tramos entre puntos seguidos, para colorear cada tramo por su velocidad.
function preparar(filas) {
  const patente = estado.filtros.patente;
  const pts = filas.map(f => ({ tipo: "punto", pos: [f.x, f.y], t: f.t, hora: f.hora, v: f.v,
                                recorrido: f.recorrido, viaje: f.viaje, patente: f.patente ?? patente }));
  const viajes = [...agrupar(pts, "viaje").values()];
  const caminos = [], tramos = [];
  for (const ps of viajes) {
    const { recorrido, patente: bus } = ps[0];
    let seguidos = [ps[0]];  // puntos sin hueco de señal entre ellos
    const cerrar = () => {   // un punto suelto (ej. un salto de GPS) queda solo como punto
      if (seguidos.length > 1) caminos.push({ tipo: "camino", path: seguidos.map(posicion), ts: seguidos.map(horaDe),
                                              recorrido, patente: bus, inicio: seguidos[0].hora, fin: seguidos.at(-1).hora });
    };
    for (let k = 1; k < ps.length; k++) {
      const a = ps[k - 1], b = ps[k];
      if (b.t - a.t > HUECO_LINEA) { cerrar(); seguidos = [b]; continue; }
      seguidos.push(b);
      tramos.push({ tipo: "tramo", path: [a.pos, b.pos], ts: [a.t, b.t], v: (a.v + b.v) / 2, recorrido, patente: bus,
                    inicio: a.hora, fin: b.hora });
    }
    cerrar();
  }
  // Recorridos de más a menos puntos: los primeros toman color (colores.js)
  tray.puntosPorRec = contar(pts, "recorrido");
  tray.recs = [...tray.puntosPorRec.keys()].sort((a, b) => tray.puntosPorRec.get(b) - tray.puntosPorRec.get(a));
  tray.colores = coloresPorRecorrido(tray.recs, fueraDeServicio);
  tray.puntosPorBus = contar(pts, "patente");
  tray.buses = tray.varios ? [...tray.puntosPorBus.keys()] : [];
  tray.caminosPorBus = agrupar(caminos, "patente");
  tray.tramosPorBus = agrupar(tramos, "patente");
  Object.assign(tray, { pts, viajes, caminos, tramos, version: tray.version + 1 });
  let t0 = Infinity, t1 = -Infinity;  // sin Math.min(...t): con cientos de miles de puntos desborda la pila
  for (const p of pts) { if (p.t < t0) t0 = p.t; if (p.t > t1) t1 = p.t; }
  $("trayTiempo").min = pts.length ? t0 : 0;
  $("trayTiempo").max = pts.length ? t1 : 86400;
  $("trayTiempo").value = $("trayTiempo").min;
}

export function capasTrayecto() {
  const t = tray.tiempo, enAnim = t != null, ahora = enAnim ? t : SIN_LIMITE;
  const porVelocidad = colorActual() === "velocidad";
  const lineas = porVelocidad ? tray.tramos : tray.caminos;
  const colores = { getColor: [tray.version, colorActual()] };
  const viaje = { getPath: camino, getTimestamps: tiempos, currentTime: ahora, fadeTrail: false,
                  widthUnits: "pixels", capRounded: true, jointRounded: true };
  const capas = [
    // Durante la reproducción, el día completo en gris tenue, de contexto
    new deck.PathLayer({ id: "tray-fondo", data: tray.caminos, getPath: camino, getColor: [150, 152, 155, 70],
      getWidth: 3, widthUnits: "pixels", jointRounded: true, capRounded: true, visible: enAnim }),
    // Lo recorrido hasta la hora (sin reproducción, el día completo): borde oscuro + color
    new deck.TripsLayer({ ...viaje, id: "tray-borde", data: lineas, trailLength: SIN_LIMITE, getColor: COLOR_BORDE, getWidth: 5 }),
    new deck.TripsLayer({ ...viaje, id: "tray-linea", data: lineas, trailLength: SIN_LIMITE, getColor: colorDe, getWidth: 3,
      pickable: true, updateTriggers: colores }),
    // Estela: los últimos 20 min, más gruesos
    new deck.TripsLayer({ ...viaje, id: "tray-estela-borde", data: lineas, trailLength: ESTELA, getColor: [255, 255, 255],
      getWidth: 9, visible: enAnim }),
    new deck.TripsLayer({ ...viaje, id: "tray-estela", data: lineas, trailLength: ESTELA, getColor: colorDe, getWidth: 5,
      visible: enAnim, updateTriggers: colores }),
    // Cada punto GPS (tooltip con hora, recorrido y velocidad); en la reproducción, solo los de la estela
    new deck.ScatterplotLayer({ id: "tray-puntos", data: tray.pts, getPosition: posicion, getFillColor: colorDe,
      getRadius: tray.varios ? 2.5 : 3.5, radiusUnits: "pixels", stroked: true, getLineColor: [255, 255, 255],
      lineWidthMinPixels: 1, pickable: true, autoHighlight: true, highlightColor: [0, 0, 0, 160],
      extensions: [filtroTiempo], getFilterValue: horaDe, filterRange: enAnim ? [t - ESTELA, t] : [-SIN_LIMITE, SIN_LIMITE],
      updateTriggers: { getFillColor: colores.getColor } }),
  ];
  // Bus resaltado (mouse sobre uno de sus puntos o sobre su nombre en la lista): su trayecto encima, grueso
  const resaltado = tray.varios && tray.resaltado
    ? (porVelocidad ? tray.tramosPorBus : tray.caminosPorBus).get(tray.resaltado) || [] : [];
  if (resaltado.length) {
    capas.push(new deck.TripsLayer({ ...viaje, id: "tray-resaltado-borde", data: resaltado, trailLength: SIN_LIMITE,
      getColor: [0, 0, 0], getWidth: 9 }));
    capas.push(new deck.TripsLayer({ ...viaje, id: "tray-resaltado", data: resaltado, trailLength: SIN_LIMITE,
      getColor: colorDe, getWidth: 6, updateTriggers: colores }));
  }
  if (enAnim) {
    const actual = enMarcha(t);
    // Seleccionable: son puntos GPS (interpolados), así que comparten tooltip y, con varios buses, el clic
    // para ver solo ese bus
    capas.push(...capasMicro(actual));
    if (tray.etiquetas) capas.push(capaEtiquetas(actual));
  }
  return capas;
}

// Último índice con ps[i].t <= t (ps ordenado por hora), o -1
function ultimoHasta(ps, t) {
  let lo = -1, hi = ps.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (ps[m].t <= t) lo = m; else hi = m - 1; }
  return lo;
}

// Posición de cada bus a la hora t, interpolada entre sus dos puntos GPS: se mueve continuo, junto con la
// punta de su línea, en vez de saltar cada 30 s (salvo en un hueco de señal: ahí espera en su último
// punto). Con varios buses, solo los que están en un viaje (ya partieron, no terminaron y tuvieron señal
// hace poco); con uno, su última posición conocida.
function enMarcha(t) {
  const actual = [];
  for (const ps of tray.viajes) {
    if (ps.length < 2 || ps[0].t > t || ps.at(-1).t <= t) continue;
    const i = ultimoHasta(ps, t), a = ps[i], b = ps[i + 1];  // a.t <= t < b.t
    if (t - a.t > SENAL_RECIENTE) continue;
    const f = b.t - a.t <= HUECO_LINEA ? (t - a.t) / (b.t - a.t) : 0;  // en un hueco de señal, en su último punto
    actual.push({ ...a, pos: [a.pos[0] + (b.pos[0] - a.pos[0]) * f, a.pos[1] + (b.pos[1] - a.pos[1]) * f],
                  hora: hhmm(t), v: a.v + (b.v - a.v) * f });
  }
  if (!tray.varios && !actual.length) {
    const i = ultimoHasta(tray.pts, t);
    if (i >= 0) actual.push(tray.pts[i]);
  }
  return actual;
}

// ── Cada bus en marcha: una micro vista de lado (blanca, con borde y franja rojos y ruedas negras) con su
// número de recorrido sobre la carrocería, como en las apps de transporte. Dibujo propio en SVG (no es el
// ícono de ninguna app). La patente va en una etiqueta encima (capaEtiquetas) y en el tooltip.
const ROJO = "#CF152D", TINTA = "#1d1d1b";
const MICRO_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60" viewBox="0 0 120 60">
<path d="M9 5H103Q114 5 115.5 16L116.5 40Q116.5 45 111 45H9Q4 45 4 40V10Q4 5 9 5Z" fill="#fff" stroke="${ROJO}" stroke-width="4"/>
<path d="M105 7.5Q112.5 8.5 113.5 17L114 30H105Z" fill="${ROJO}"/>
<rect x="6" y="36" width="108" height="7.5" rx="2.5" fill="${ROJO}"/>
<circle cx="29" cy="46" r="8.5" fill="${TINTA}" stroke="#fff" stroke-width="2.5"/>
<circle cx="90" cy="46" r="8.5" fill="${TINTA}" stroke="#fff" stroke-width="2.5"/>
</svg>`;
// Alto del ícono en píxeles y dónde va el número (centro de la parte blanca, en unidades del SVG de 120 × 60)
const MICRO = { alto: { uno: 36, varios: 26 }, centroTexto: [-6, -10], tamTexto: 0.5 };
const ATLAS_MICRO = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(MICRO_SVG);
const MAPA_MICRO = { micro: { x: 0, y: 0, width: 120, height: 60, anchorY: 30, mask: false } };
const iconoMicro = () => "micro";
// "T405 00I" → "405" (como se publica el servicio); "B20 00R" → "B20"; sin recorrido → nada
export const numeroRecorrido = r => {
  if (!r || r === "(sin recorrido)") return "";
  const s = String(r).split(" ")[0];
  return /^T\d/.test(s) ? s.slice(1) : s;
};
const numeroDe = p => numeroRecorrido(p.recorrido);

function capasMicro(actual) {
  const alto = tray.varios ? MICRO.alto.varios : MICRO.alto.uno, escala = alto / 60;
  return [
    // Seleccionable: son puntos GPS (interpolados), así que comparten tooltip y, con varios buses, el clic
    // para ver solo ese bus
    new deck.IconLayer({
      id: "tray-bus", data: actual, getPosition: posicion, getIcon: iconoMicro, iconAtlas: ATLAS_MICRO,
      iconMapping: MAPA_MICRO, getSize: alto, sizeUnits: "pixels",
      pickable: true, autoHighlight: true, highlightColor: [0, 0, 0, 60],
    }),
    new deck.TextLayer({
      id: "tray-bus-numero", data: actual, getPosition: posicion, getText: numeroDe,
      getSize: Math.round(alto * MICRO.tamTexto), sizeUnits: "pixels",
      getPixelOffset: [MICRO.centroTexto[0] * escala, MICRO.centroTexto[1] * escala],
      getTextAnchor: "middle", getAlignmentBaseline: "center",
      fontFamily: "'Open Sans', Roboto, sans-serif", fontWeight: 800, getColor: [29, 29, 27, 255],
    }),
  ];
}

// Patente de cada bus en marcha, encima de su micro
const ETIQUETA = { alto: 18, anchoCaracter: 7.5, relleno: 10,
                   separacion: { uno: MICRO.alto.uno / 2 + 3, varios: MICRO.alto.varios / 2 + 3 } };

function capaEtiquetas(actual) {
  const texto = p => p.patente || "";
  const arriba = tray.varios ? ETIQUETA.separacion.varios : ETIQUETA.separacion.uno;
  return new deck.TextLayer({
    id: "tray-etiquetas", data: sinSolaparse(actual, texto, arriba), getPosition: posicion, getText: texto,
    getSize: 12, sizeUnits: "pixels", getPixelOffset: [0, -arriba],
    getTextAnchor: "middle", getAlignmentBaseline: "bottom",
    fontFamily: "'Open Sans', Roboto, sans-serif", fontWeight: 700, getColor: [0, 0, 0, 255],
    background: true, getBackgroundColor: [255, 255, 255, 235], backgroundPadding: [4, 1],
    getBorderColor: [0, 0, 0, 90], getBorderWidth: 1,
  });
}

// Con muchos buses juntos (ej. en un terminal), las etiquetas que chocarían con otra ya puesta se omiten;
// al acercar el mapa hay espacio y aparecen. Se resuelve en píxeles de pantalla, con una prioridad fija
// (el orden de los buses) para que una etiqueta no parpadee entre un cuadro y el siguiente. Los buses sin
// etiqueta igual muestran su patente al pasar el mouse. (No se usa el CollisionFilterExtension de deck.gl:
// con la capa recreada en cada cuadro de la reproducción ocultaba todas las etiquetas.)
function sinSolaparse(puntos, texto, arriba) {
  const prioridad = p => tray.varios ? tray.buses.indexOf(p.patente) : 0;
  const puestas = [], visibles = [];
  for (const p of [...puntos].sort((a, b) => prioridad(a) - prioridad(b))) {
    const { x, y } = vista.mapa.project(p.pos);
    const ancho = texto(p).length * ETIQUETA.anchoCaracter + ETIQUETA.relleno;
    const r = { x0: x - ancho / 2, x1: x + ancho / 2, y0: y - arriba - ETIQUETA.alto, y1: y - arriba };
    if (puestas.some(o => r.x0 < o.x1 && r.x1 > o.x0 && r.y0 < o.y1 && r.y1 > o.y0)) continue;
    puestas.push(r); visibles.push(p);
  }
  return visibles;
}

function redibujarTrayecto() {
  vista.overlay.setProps({ layers: conReferencia(capasTrayecto()) });
  const texto = tray.tiempo == null ? "todo" : hhmm(tray.tiempo);
  if ($("trayHora").textContent !== texto) $("trayHora").textContent = texto;
  $("trayTiempo").setAttribute("aria-valuetext", tray.tiempo == null ? "día completo" : hhmm(tray.tiempo));
  iconoPlay("trayPlay", !!tray.timer);
}

// Sin reproducción en curso nada redibuja solo: mostrar u ocultar las patentes, resaltar un bus o mover el
// mapa (cambia qué etiquetas caben) redibuja aquí. Reproduciendo, lo toma el próximo cuadro.
export function redibujarSiQuieto() {
  if (!tray.timer) redibujarTrayecto();
}

export function mostrarEtiquetas(si) {
  tray.etiquetas = si;
  redibujarSiQuieto();
}

export function resaltarBus(patente) {
  if (!tray.varios || tray.resaltado === (patente || null)) return;
  tray.resaltado = patente || null;
  redibujarSiQuieto();
}

export function colorearPor(modo) {
  tray.colorPor = modo;
  redibujarSiQuieto();
}

export function pararAnimacion() {
  if (tray.timer) { cancelAnimationFrame(tray.timer); tray.timer = null; }
  iconoPlay("trayPlay", false);
}

export function irA(t) {
  pararAnimacion(); tray.tiempo = t; redibujarTrayecto();
}

// Reproducir / pausar. Con requestAnimationFrame: un cuadro por refresco de pantalla, sin acumular cuadros
// atrasados si uno tarda (con setInterval se encolaban), y se detiene solo con la pestaña oculta.
export function alternarReproduccion() {
  if (tray.timer) return pararAnimacion();
  const min = +$("trayTiempo").min, max = +$("trayTiempo").max;
  if (tray.tiempo == null || tray.tiempo >= max) tray.tiempo = min;
  // avanza según el tiempo real transcurrido: la velocidad se lee en cada cuadro, así que cambiarla durante
  // la reproducción rige al tiro
  let antes = performance.now();
  const cuadro = ahora => {
    tray.tiempo = Math.min(max, tray.tiempo + Number($("trayVel").value) * Math.max(0, ahora - antes) / 1000);
    antes = ahora;
    $("trayTiempo").value = tray.tiempo;
    if (tray.tiempo >= max) { pararAnimacion(); redibujarTrayecto(); return; }
    tray.timer = requestAnimationFrame(cuadro);
    redibujarTrayecto();
  };
  tray.timer = requestAnimationFrame(cuadro);
  redibujarTrayecto();
}

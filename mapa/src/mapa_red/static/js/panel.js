// Panel principal: modo de vista, leyendas, cifras y redibujo del mapa con lo ya cargado.
import { iconoPlay } from "./avisos.js";
import { calcular } from "./calor.js";
import { capas, colorRec } from "./capas.js";
import { COLOR_OTROS, METRICAS, PALETA, RAMPA } from "./colores.js";
import { METROS_POR_GRADO, marcaCodigo } from "./dominio.js";
import { datos, estado, tipicas, tray, vista } from "./estado.js";
import { ubicarReproductor } from "./mapa.js";
import { pintarRanking } from "./ranking.js";
import { conReferencia } from "./referencia.js";
import { cargarTipicas } from "./tipicas.js";
import { colorActual, colorRecorrido, nombreFiltro, pararAnimacion } from "./trayecto.js";
import { guardarUrl } from "./url.js";
import { $, esc, fmt } from "./util.js";

const MAX_BUSES_LISTA = 60;
const PASO_HORA_MS = 900;  // animación por hora: una hora cada 0,9 s
const gradiente = paradas => `linear-gradient(90deg, ${paradas.map(c => `rgb(${c})`).join(",")})`;

// Muestra los controles y la leyenda del modo actual
export function leyenda() {
  const enTray = estado.modo === "trayecto";
  const enRec = estado.modo === "recorrido" && !!datos.porRec;
  $("leyenda").hidden = enRec || enTray;
  $("leyendaRec").hidden = !enRec;
  $("trayPanel").hidden = !enTray;
  $("ctrlCeldas").hidden = $("ctrlRadio").hidden = enRec || enTray;
  $("ctrlMinObs").hidden = enTray;
  const conReproductor = enTray && tray.pts.length > 0;
  if ($("reproductor").hidden === conReproductor) {
    $("reproductor").hidden = !conReproductor;
    if (conReproductor) ubicarReproductor();
  }
  if (enTray) return leyendaTrayecto();
  if (enRec) return leyendaRecorridos();
  const m = METRICAS[estado.metrica];
  $("barra").style.background = gradiente(m.invertir ? [...RAMPA].reverse() : RAMPA);
  $("legMin").textContent = "≤ " + m.dominio[0];
  $("legMax").textContent = "≥ " + m.dominio[1];
  $("legTit").textContent = m.titulo;
}

const filaLeyenda = (color, nombre, dato) =>
  `<div class="rec"><span class="color" style="background:rgb(${color})"></span>` +
  `<span class="nombre">${nombre}</span><span class="dato">${dato}</span></div>`;

function leyendaTrayecto() {
  const porVelocidad = colorActual() === "velocidad";
  for (const r of document.querySelectorAll('input[name="trayColor"]')) r.checked = r.value === colorActual();
  $("trayLeyendaVel").hidden = !porVelocidad;
  $("trayLeyendaVel").querySelector(".barra").style.background = gradiente(RAMPA);
  // Por recorrido: un color por recorrido (los 5 con más puntos en servicio); el resto y "fuera de servicio" en gris
  const hayGris = tray.recs.some(r => colorRecorrido(r) === COLOR_OTROS);
  $("trayRecs").hidden = porVelocidad;
  $("trayRecs").innerHTML = porVelocidad ? "" :
    (hayGris ? `<div class="nota" style="margin:0 0 4px">En gris: fuera de servicio y los recorridos con menos puntos.</div>` : "") +
    tray.recs.map(r => filaLeyenda(colorRecorrido(r), esc(r) + marcaCodigo(r), `${fmt(tray.puntosPorRec.get(r), 0)} puntos`)).join("");

  if (tray.varios) {
    $("trayResumen").textContent = tray.pts.length
      ? `${fmt(tray.buses.length, 0)} buses hicieron ${nombreFiltro()} ese día, en ${fmt(tray.viajes.length, 0)} viajes ` +
        `(${fmt(tray.pts.length, 0)} puntos GPS). En la reproducción, cada micro es un bus en marcha, con su número de recorrido y ` +
        "patente. Pasa el mouse por un bus (en el mapa o en la lista) para resaltar su trayecto; clic para verlo solo."
      : "Sin puntos para lo filtrado ese día.";
    $("trayBuses").innerHTML = tray.buses.length ? `<h3 class="sub" style="margin:12px 0 0">Buses (${fmt(tray.buses.length, 0)})</h3>
      <div class="lista-buses">${tray.buses.slice(0, MAX_BUSES_LISTA).map(b =>
        `<button type="button" class="bus-btn" data-patente="${esc(b)}" title="Ver solo ${esc(b)}">${esc(b)}` +
        `<span class="dato">${fmt(tray.puntosPorBus.get(b), 0)}</span></button>`).join("")}</div>` +
      (tray.buses.length > MAX_BUSES_LISTA ? `<div class="nota">y ${fmt(tray.buses.length - MAX_BUSES_LISTA, 0)} buses más (elige uno en el filtro Bus)</div>` : "") : "";
    return;
  }
  $("trayBuses").innerHTML = "";
  const parte = nombreFiltro();  // con servicio / sentido / recorrido filtrado, solo esa parte del día
  $("trayResumen").textContent = tray.pts.length
    ? `${fmt(tray.pts.length, 0)} puntos GPS${parte ? ` de ${parte}` : ""} en ${fmt(tray.viajes.length, 0)} viajes` +
      `${parte ? " (quita el filtro para ver el día completo del bus)" : ""}. ` +
      "En la reproducción, la micro es el bus, con su número de recorrido encima."
    : `Sin puntos${parte ? ` de ${parte}` : ""} para este bus y día.`;
}

function leyendaRecorridos() {
  const { recorridos } = datos.porRec;
  const hayOtros = recorridos.some(r => r.id === "Otros");
  $("leyendaRec").innerHTML = `<div class="nota">Cada color es un recorrido${hayOtros
    ? ` (los ${PALETA.length} con más registros; el resto, en gris)` : ""}. Todo el período y todas las horas.
    <b>Línea:</b> trayectoria típica de cada recorrido, un viaje real y representativo (pasa el mouse para
    ver bus, día y hora)${tipicas.cargando ? " — cargando…" : ""}.</div>` +
    recorridos.map((r, i) => filaLeyenda(colorRec(i), esc(r.id),
      r.otros ? `${fmt(r.otros, 0)} recorridos` : `${fmt(r.n, 0)} reg.`)).join("");
}

// Redibuja el mapa, la leyenda y las cifras con lo ya cargado (sin pedir nada al servidor)
export function refrescar() {
  const { celdas, total } = calcular();
  datos.ultimasCeldas = celdas;
  vista.overlay.setProps({ layers: conReferencia(capas(celdas)) });
  leyenda();
  const hh = String(estado.hora).padStart(2, "0");
  const celda = `celdas de ~${Math.round(CONFIG.tam * METROS_POR_GRADO)} m`;
  $("estado").textContent = estado.modo === "trayecto"
    ? `${tray.varios ? `${nombreFiltro()} · ${fmt(tray.buses.length, 0)} buses` : `Bus ${estado.filtros.patente}`} · ${tray.dia || "sin días"}`
    : estado.modo === "recorrido" && datos.porRec
    ? `${datos.porRec.recorridos.length} recorridos · ${celda}`
    : `${fmt(celdas.length, 0)} ${celda} · ${estado.todoDia ? "todo el día" : `${hh}:00–${hh}:59`}`;
  const sinDatos = !total.n;  // sin registros, "–" en vez de un 0 que parece un dato
  $("resumen").innerHTML = `
    <div class="kpi"><b>${sinDatos ? "–" : fmt(total.vel_mov)} km/h</b><span>vel. en movimiento</span></div>
    <div class="kpi"><b>${sinDatos ? "–" : fmt(total.pct_det, 0)} %</b><span>registros detenido</span></div>
    ${total.vel_com != null ? `<div class="kpi kpi-ancho"><b>${fmt(total.vel_com)} km/h</b>
      <span>vel. comercial en ruta (km recorridos / tiempo, sin esperas en terminal)</span></div>` : ""}
    <div class="kpi"><b>${fmt(datos.META.buses, 0)}</b><span>buses</span></div>
    <div class="kpi"><b>${fmt(total.n, 0)}</b><span>registros</span></div>`;
  $("horaTxt").textContent = estado.todoDia ? "—" : `${hh}:00`;
  $("hora").setAttribute("aria-valuetext", estado.todoDia ? "todo el día" : `${hh}:00 a ${hh}:59`);
  $("minObsTxt").textContent = estado.minObs;
  $("radioTxt").textContent = estado.radio;
  guardarUrl();
}

// ── Modo de vista ──
export function setModo(modo, redibujar = true) {
  if (modo !== "trayecto") pararAnimacion();
  if (modo !== "calor") pararHora();
  estado.modo = modo;
  document.querySelectorAll("[data-modo]").forEach(x => {
    x.classList.toggle("activo", x.dataset.modo === modo);
    x.setAttribute("aria-pressed", x.dataset.modo === modo);
  });
  if (redibujar && datos.C) refrescar();
  if (datos.rankingFilas.length) pintarRanking();  // los puntos del ranking toman el color de recorrido
  if (datos.C) cargarTipicas();  // solo pide algo si el modo y los filtros lo usan
}

// ── Animación por hora del mapa de calor (solo en Calor: al cambiar de modo se detiene) ──
let timerHora = null;
export function pararHora() {
  if (timerHora) { clearInterval(timerHora); timerHora = null; }
  iconoPlay("play", false);
}
export function alternarAnimacionHora() {
  if (timerHora) return pararHora();
  estado.todoDia = false; $("todoDia").checked = false; $("hora").disabled = false;
  iconoPlay("play", true);
  timerHora = setInterval(() => {
    estado.hora = (estado.hora + 1) % 24; $("hora").value = estado.hora; refrescar();
  }, PASO_HORA_MS);
}

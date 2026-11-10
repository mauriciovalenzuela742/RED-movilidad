// Capas de referencia del proyecto RED-movilidad, encima o debajo de cualquier vista (calor, recorridos,
// trayecto): trazados GTFS, pendiente de las calles (GTFS + DEM Copernicus GLO-30), paraderos GTFS y
// cargadores eléctricos (OSM). Las exporta `python -m pulso.capas_mapa` y el servidor ofrece las que
// existan (CONFIG.capas). Cada una se pide la primera vez que se activa.
//
// Rendimiento: conReferencia() corre en cada cuadro de las animaciones. Las capas reciben siempre los mismos
// arreglos (memorizados por filtro) y accesores de módulo, así deck.gl no recalcula atributos por cuadro.
import { estado } from "./estado.js";
import { esc, fmt, pedirJson } from "./util.js";

export const CAPAS_REF = {
  trazados:   { nombre: "Trazados GTFS", nota: "Trazado oficial de cada recorrido (GTFS vigente). Con un servicio filtrado, solo el suyo." },
  pendiente:  { nombre: "Pendiente de las calles", nota: "Tramos de ~100 m por donde pasan buses, con su pendiente (GTFS + DEM satelital Copernicus GLO-30)." },
  paraderos:  { nombre: "Paraderos", nota: "Paraderos GTFS. Con un servicio filtrado, solo los suyos." },
  cargadores: { nombre: "Cargadores eléctricos", nota: "Estaciones de carga de vehículos eléctricos de la RM (OpenStreetMap)." },
};

// Pendiente (valor absoluto, %): rampa ordinal de un solo tono (naranja), distinta de la de velocidad
// (rojo-gris-azul). Validada con la skill de dataviz (--ordinal, superficie #f5f5f4): monótona, saltos
// visibles y el extremo claro a 2,17:1 del mapa.
export const PENDIENTE = [
  { hasta: 1, color: [240, 144, 96], etiqueta: "< 1 %" },
  { hasta: 2, color: [225, 97, 46], etiqueta: "1–2 %" },
  { hasta: 4, color: [191, 75, 29], etiqueta: "2–4 %" },
  { hasta: 6, color: [149, 55, 19], etiqueta: "4–6 %" },
  { hasta: Infinity, color: [102, 36, 10], etiqueta: "≥ 6 %" },
];
const COLOR_TRAZADO = [72, 72, 70];          // tinta oscura: el trazado se lee sobre el calor y el mapa claro
const COLOR_CARGADOR = [98, 80, 214];        // violeta: no se usa en la paleta de recorridos ni en la de velocidad
const BLANCO = [255, 255, 255];

export const ref = {
  disponibles: (window.CONFIG?.capas || []).filter(c => c in CAPAS_REF),
  activas: new Set(),
  datos: {},          // nombre → arreglo cargado
  cargando: new Set(),
};

const clasePendiente = p => PENDIENTE.find(c => Math.abs(p) < c.hasta);

// Activa o desactiva una capa; al activarla por primera vez la pide al servidor. `alCargar` redibuja.
export async function alternarCapa(nombre, activa, alCargar) {
  if (!ref.disponibles.includes(nombre)) return;
  if (!activa) { ref.activas.delete(nombre); return alCargar(); }
  ref.activas.add(nombre);
  if (ref.datos[nombre] || ref.cargando.has(nombre)) return alCargar();
  ref.cargando.add(nombre);
  alCargar();  // muestra "cargando…" en el panel
  try {
    const filas = await pedirJson(`/capas/${nombre}.json`);
    for (const f of filas) f.tipo = "ref-" + nombre;  // para el tooltip
    if (nombre === "pendiente") for (const f of filas) f.color = clasePendiente(f.p).color;
    ref.datos[nombre] = filas;
  } catch (e) {
    ref.activas.delete(nombre);
    throw e;
  } finally {
    ref.cargando.delete(nombre);
    alCargar();
  }
}

// Servicio y sentido filtrados (del filtro de servicio / sentido, o del recorrido "T405 00I" → T405, I)
function filtroServicio() {
  const { servicio, sentido, recorrido } = estado.filtros;
  const rec = recorrido?.match(/^(\S+).*\d{2}([IR])$/);
  return { servicio: servicio || rec?.[1] || null, sentido: sentido || rec?.[2] || null };
}

// Subconjunto filtrado, memorizado por clave: el mismo arreglo mientras no cambie el filtro
const memo = new Map();
function filtrado(nombre, clave, fn) {
  const k = `${nombre}|${clave}`;
  if (memo.get(nombre)?.k !== k) memo.set(nombre, { k, v: fn(ref.datos[nombre]) });
  return memo.get(nombre).v;
}

function trazadosVisibles() {
  const { servicio, sentido } = filtroServicio();
  if (!servicio) return ref.datos.trazados;
  return filtrado("trazados", `${servicio}|${sentido}`,
    d => d.filter(t => t.servicio === servicio && (!sentido || t.sentido === sentido)));
}

function paraderosVisibles() {
  const { servicio } = filtroServicio();
  if (!servicio) return ref.datos.paraderos;
  return filtrado("paraderos", servicio, d => d.filter(p => p.servicios.includes(servicio)));
}

const activa = n => ref.activas.has(n) && !!ref.datos[n];
const conFiltro = () => !!filtroServicio().servicio;

// Accesores de módulo (los mismos en cada cuadro)
const camino = d => d.camino, desde = d => d.a, hasta = d => d.b, colorPend = d => d.color, pos = d => d.pos;

// Debajo de los datos: pendiente y trazados
function capasAbajo() {
  const capas = [];
  if (activa("pendiente")) capas.push(new deck.LineLayer({
    id: "ref-pendiente", data: ref.datos.pendiente, getSourcePosition: desde, getTargetPosition: hasta,
    getColor: colorPend, getWidth: 3, widthUnits: "pixels", widthMinPixels: 1.5, pickable: true,
    autoHighlight: true, highlightColor: [0, 0, 0, 120],
  }));
  if (activa("trazados")) {
    const filtrados = conFiltro();
    capas.push(new deck.PathLayer({
      id: "ref-trazados", data: trazadosVisibles(), getPath: camino,
      getColor: [...COLOR_TRAZADO, filtrados ? 230 : 90], getWidth: filtrados ? 3 : 1.2, widthUnits: "pixels",
      jointRounded: true, capRounded: true, pickable: true, autoHighlight: true, highlightColor: [0, 0, 0, 160],
      updateTriggers: { getColor: [filtrados], getWidth: [filtrados] },
    }));
  }
  return capas;
}

// Encima de los datos: paraderos y cargadores (puntos chicos con borde, para no tapar el calor)
function capasArriba() {
  const capas = [];
  if (activa("paraderos")) capas.push(new deck.ScatterplotLayer({
    id: "ref-paraderos", data: paraderosVisibles(), getPosition: pos, getRadius: 9, radiusUnits: "meters",
    radiusMinPixels: 1.5, radiusMaxPixels: 6, getFillColor: BLANCO, stroked: true, getLineColor: COLOR_TRAZADO,
    lineWidthUnits: "pixels", getLineWidth: 1, pickable: true, autoHighlight: true, highlightColor: [0, 0, 0, 160],
  }));
  if (activa("cargadores")) capas.push(new deck.ScatterplotLayer({
    id: "ref-cargadores", data: ref.datos.cargadores, getPosition: pos, getRadius: 6, radiusUnits: "pixels",
    getFillColor: COLOR_CARGADOR, stroked: true, getLineColor: BLANCO, lineWidthUnits: "pixels", getLineWidth: 2,
    pickable: true, autoHighlight: true, highlightColor: [0, 0, 0, 120],
  }));
  return capas;
}

// Las capas del modo actual con las de referencia debajo y encima
export const conReferencia = capasModo =>
  ref.activas.size ? [...capasAbajo(), ...capasModo, ...capasArriba()] : capasModo;

// Contenido del tooltip de un elemento de referencia (o null si no es de referencia)
export function tooltipReferencia(d) {
  switch (d.tipo) {
    case "ref-trazados":
      return `<b>Trazado GTFS ${esc(d.id)}</b><br>Servicio ${esc(d.servicio)}, sentido ${esc(d.sentido)} · ${fmt(d.km)} km`;
    case "ref-pendiente":
      return `<b>Pendiente:</b> ${fmt(Math.abs(d.p))} % ${d.p > 0 ? "(sube en el sentido del tramo)" : d.p < 0 ? "(baja en el sentido del tramo)" : ""}<br>` +
        `Recorridos que pasan: ${fmt(d.n, 0)}<br><span style="color:#58595B">GTFS + DEM Copernicus GLO-30</span>`;
    case "ref-paraderos":
      return `<b>${esc(d.id)}</b> · ${esc(d.nombre)}<br>Servicios: ${esc(d.servicios.slice(0, 12).join(", "))}` +
        (d.servicios.length > 12 ? ` y ${d.servicios.length - 12} más` : "");
    case "ref-cargadores":
      return `<b>${esc(d.nombre)}</b>${d.operador && d.operador !== d.nombre ? `<br>Operador: ${esc(d.operador)}` : ""}` +
        (d.capacidad ? `<br>Capacidad: ${esc(d.capacidad)}` : "") +
        (d.enchufes.length ? `<br>Enchufes: ${esc(d.enchufes.join(", "))}` : "") +
        `<br><span style="color:#58595B">OpenStreetMap</span>`;
    default:
      return null;
  }
}

// Panel: una casilla por capa disponible, su estado de carga y la leyenda de pendiente
export function pintarPanelReferencia(contenedor) {
  if (!ref.disponibles.length) { contenedor.hidden = true; return; }
  contenedor.hidden = false;
  const { servicio } = filtroServicio();
  contenedor.querySelector(".lista").innerHTML = ref.disponibles.map(n => {
    const c = CAPAS_REF[n], on = ref.activas.has(n);
    const extra = ref.cargando.has(n) ? " — cargando…"
      : on && servicio && (n === "trazados" || n === "paraderos")
        ? ` — ${fmt((n === "trazados" ? trazadosVisibles() : paraderosVisibles())?.length ?? 0, 0)} de ${esc(servicio)}` : "";
    return `<label class="chk" title="${esc(c.nota)}"><input type="checkbox" data-capa="${n}"${on ? " checked" : ""}>` +
      `${n === "cargadores" ? `<span class="punto-ref" style="background:rgb(${COLOR_CARGADOR})"></span>` : ""}${esc(c.nombre)}${extra}</label>`;
  }).join("");
  const leyenda = contenedor.querySelector(".leyenda-pend");
  leyenda.hidden = !activa("pendiente");
  leyenda.innerHTML = `<div class="nota" style="margin:4px 0">Pendiente (valor absoluto)</div>` +
    PENDIENTE.map(c => `<div class="rec"><span class="color" style="border-radius:2px;background:rgb(${c.color})"></span>` +
      `<span class="nombre">${c.etiqueta}</span></div>`).join("");
  const nota = contenedor.querySelector(".nota-ref");
  nota.hidden = !(activa("trazados") || activa("paraderos"));
  nota.textContent = "Servicio GTFS → servicio del GPS con una regla provisional (\"405\" → \"T405\"); " +
    "la traducción definitiva está pendiente de revisar con los textos de ruta reales.";
}

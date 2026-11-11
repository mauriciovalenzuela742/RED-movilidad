// Punto de entrada: conecta los controles de la página con el estado y arranca.
//
//   estado.js    estado compartido (lo elegido, lo cargado, el mapa)
//   filtros.js   aplicar filtros: consultas al servidor y redibujo
//   panel.js     modo de vista, leyendas, cifras y redibujo con lo ya cargado
//   calor.js · capas.js · trayecto.js · tipicas.js · ranking.js · mapa.js   cada parte de la vista
//   combobox.js  desplegables con buscador · url.js  estado en la URL · avisos.js  carga y errores
import { activarAvisos, conCarga, mostrarError } from "./avisos.js";
import { mejorarSelects } from "./combobox.js";
import { DIMENSIONES, ETIQUETA, textoValor } from "./dominio.js";
import { datos, estado, tray, vista } from "./estado.js";
import { aplicarFiltros } from "./filtros.js";
import { crearMapa, encuadrarTrayecto, ubicarReproductor } from "./mapa.js";
import { alternarAnimacionHora, pararHora, refrescar, setModo } from "./panel.js";
import { ordenarPor, pintarRanking } from "./ranking.js";
import { alternarCapa, pintarPanelReferencia } from "./referencia.js";
import {
  alternarReproduccion, cargarDias, cargarTrayecto, colorearPor, irA, mostrarEtiquetas, pararAnimacion,
  redibujarSiQuieto, resaltarBus,
} from "./trayecto.js";
import { capasDeUrl, leerUrl } from "./url.js";
import { $ } from "./util.js";

// Carga con aviso y, si falla, error con reintentar; después redibuja y encuadra el trayecto (y el
// ranking: sus puntos toman los colores de los recorridos del día)
function cargarYMostrarTrayecto(cargar) {
  const intento = () => conCarga(cargar).then(() => { refrescar(); pintarRanking(); encuadrarTrayecto(); })
    .catch(e => mostrarError(e, intento));
  intento();
}

function controlesFiltros() {
  for (const d of DIMENSIONES) {
    // Si la búsqueda no encuentra nada porque otro filtro acota la lista, ofrecer buscar en todos
    $("f_" + d).pistaVacio = () => {
      const otros = Object.entries(estado.filtros).filter(([k]) => k !== d);
      if (!otros.length) return null;
      return {
        texto: `Solo se listan los que calzan con ${otros.map(([k, v]) => `${ETIQUETA[k].toLowerCase()} ${textoValor(k, v)}`).join(", ")}.`,
        boton: "Buscar en todos",
        accion: async () => { for (const [k] of otros) delete estado.filtros[k]; await aplicarFiltros({ encuadre: false }); },
      };
    };
    $("f_" + d).addEventListener("change", e => {
      const v = e.target.value.trim();
      if (v) estado.filtros[d] = v; else delete estado.filtros[d];
      // al elegir un bus se ve su trayecto; al elegir un servicio o recorrido, sus recorridos con colores
      if (v && d === "patente") setModo("trayecto", false);
      else if (v && (d === "servicio" || d === "recorrido") && !estado.filtros.patente) setModo("recorrido", false);
      aplicarFiltros();
    });
  }
  document.querySelectorAll("[data-limpiar]").forEach(b => b.onclick = () => {
    delete estado.filtros[b.dataset.limpiar]; aplicarFiltros();
  });
  $("limpiarTodo").onclick = () => { estado.filtros = {}; aplicarFiltros(); };
}

function controlesModo() {
  document.querySelectorAll("[data-modo]").forEach(b => b.onclick = () => {
    setModo(b.dataset.modo);
    if (b.dataset.modo !== "trayecto") return;
    if (tray.pts.length) return encuadrarTrayecto();
    // sin bus, los buses de lo filtrado se cargan al entrar al modo (solo eso; el resto ya está)
    cargarYMostrarTrayecto(cargarDias);
  });
}

function controlesTrayecto() {
  $("trayDia").onchange = e => { tray.dia = e.target.value; cargarYMostrarTrayecto(cargarTrayecto); };
  $("trayTiempo").oninput = e => irA(+e.target.value);
  $("trayTodo").onclick = () => irA(null);
  $("trayPlay").onclick = alternarReproduccion;
  $("trayEtiquetas").onchange = e => mostrarEtiquetas(e.target.checked);
  $("trayColor").onchange = e => { colorearPor(e.target.value); refrescar(); pintarRanking(); };  // + leyenda y URL
  // Lista de buses: mouse encima → resaltar su trayecto; clic → ver solo ese bus
  $("trayBuses").onmouseover = e => resaltarBus(e.target.closest("[data-patente]")?.dataset.patente);
  $("trayBuses").onmouseleave = () => resaltarBus(null);
  $("trayBuses").onfocusin = e => resaltarBus(e.target.closest("[data-patente]")?.dataset.patente);
  $("trayBuses").onclick = e => {
    const b = e.target.closest("[data-patente]");
    if (b) verSoloBus(b.dataset.patente);
  };
  // Al mover o acercar el mapa sin reproducción en curso, recalcular qué patentes caben sin taparse
  vista.mapa.on("moveend", () => { if (estado.modo === "trayecto") redibujarSiQuieto(); });
  // Barra espaciadora: reproducir / pausar, como en un reproductor (salvo escribiendo o sobre un control)
  document.addEventListener("keydown", e => {
    if (e.key !== " " || estado.modo !== "trayecto" || $("reproductor").hidden) return;
    if (e.target.closest("input, select, textarea, button, [role=listbox], [contenteditable]")) return;
    e.preventDefault();
    alternarReproduccion();
  });
}

function controlesCalor() {
  $("metrica").onchange = e => { estado.metrica = e.target.value; refrescar(); };
  $("tipoDia").onchange = e => { estado.tipoDia = e.target.value; refrescar(); };
  // Mover la hora a mano o volver a "todo el día" detiene la animación
  $("hora").oninput = e => { pararHora(); estado.hora = +e.target.value; refrescar(); };
  $("todoDia").onchange = e => {
    pararHora(); estado.todoDia = e.target.checked; $("hora").disabled = estado.todoDia; refrescar();
  };
  $("minObs").oninput = e => { estado.minObs = +e.target.value; refrescar(); };
  $("radio").oninput = e => { estado.radio = +e.target.value; refrescar(); };
  $("play").onclick = alternarAnimacionHora;
}

function controlesPaneles() {
  // Plegar el panel principal (más mapa a la vista)
  $("plegarPanel").onclick = () => {
    const plegado = $("panel").classList.toggle("plegado");
    const b = $("plegarPanel"), txt = plegado ? "Desplegar panel" : "Plegar panel";
    b.setAttribute("aria-expanded", !plegado); b.setAttribute("aria-label", txt); b.title = txt;
    b.querySelector(".ms").textContent = plegado ? "expand_more" : "expand_less";
    ubicarReproductor();  // el espacio libre al pie del mapa cambió
  };
  // Cerrar / abrir el ranking; el foco pasa al botón que queda a la vista
  const verRanking = si => {
    $("panelRanking").hidden = !si; $("abrirRanking").hidden = si;
    $("abrirRanking").setAttribute("aria-expanded", si);
    (si ? $("cerrarRanking") : $("abrirRanking")).focus({ preventScroll: true });
    ubicarReproductor();
  };
  window.addEventListener("resize", ubicarReproductor);
  $("cerrarRanking").onclick = () => verRanking(false);
  $("abrirRanking").onclick = () => verRanking(true);
  // Ranking: ordenar por columna y clic en una fila para filtrar por ese recorrido (otro clic lo quita)
  document.querySelectorAll("#panelRanking th[data-orden] button").forEach(b =>
    b.onclick = () => ordenarPor(b.parentElement.dataset.orden));
  $("tabla").onclick = e => {
    const tr = e.target.closest("tr[data-id]"); if (!tr) return;  // la fila "sin recorridos" no tiene id
    if (estado.filtros.recorrido === tr.dataset.id) delete estado.filtros.recorrido;
    else { estado.filtros.recorrido = tr.dataset.id; setModo("recorrido", false); }
    aplicarFiltros();
  };
}

// Capas de referencia (trazados GTFS, pendiente, paraderos, cargadores): casillas del panel y las de la URL
function controlesReferencia() {
  const panel = $("refPanel");
  const redibujar = () => { if (datos.C) refrescar(); pintarPanelReferencia(panel); };
  const activar = (nombre, si) =>
    alternarCapa(nombre, si, redibujar).catch(e => mostrarError(e, () => activar(nombre, true)));
  panel.addEventListener("change", e => { if (e.target.dataset.capa) activar(e.target.dataset.capa, e.target.checked); });
  pintarPanelReferencia(panel);
  for (const n of capasDeUrl()) activar(n, true);
}

// Con varios buses en Trayecto: ver solo uno (mismo día y filtros)
function verSoloBus(patente) {
  estado.filtros.patente = patente;
  pararAnimacion();
  setModo("trayecto", false);
  aplicarFiltros();
}

const esDelTrayecto = o => o && (o.tipo === "punto" || o.tipo === "tramo" || o.tipo === "camino");

// Clic en un punto, un bus en marcha o una línea de un bus → solo ese bus
function alHacerClicEnMapa(objeto) {
  if (estado.modo === "trayecto" && tray.varios && esDelTrayecto(objeto)) verSoloBus(objeto.patente);
}

// Mouse sobre algo de un bus → resaltar su trayecto completo
function alPasarSobreMapa(objeto) {
  if (estado.modo === "trayecto") resaltarBus(esDelTrayecto(objeto) ? objeto.patente : null);
}

$("periodo").textContent =
  `Período ${CONFIG.periodo} · ${CONFIG.dias_laboral} días laborales, ${CONFIG.dias_finde} fin de semana/feriado`;
crearMapa({ onClick: alHacerClicEnMapa, onHover: alPasarSobreMapa });
mejorarSelects(document.querySelectorAll("select"));
controlesFiltros();
controlesModo();
controlesTrayecto();
controlesCalor();
controlesPaneles();
activarAvisos();
leerUrl();  // filtros y vista de un enlace compartido
controlesReferencia();
aplicarFiltros({ encuadre: Object.keys(estado.filtros).length > 0 });

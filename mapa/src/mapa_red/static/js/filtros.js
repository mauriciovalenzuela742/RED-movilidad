// Filtros: listas de los desplegables y aplicación de los filtros (consultas al servidor + redibujo).
import { cargando, mostrarError, ocultarError } from "./avisos.js";
import { cargarCeldas } from "./calor.js";
import { DIMENSIONES, textoValor, tipoCodigo } from "./dominio.js";
import { datos, estado } from "./estado.js";
import { encuadrarCeldas, encuadrarTrayecto, volverAVistaInicial } from "./mapa.js";
import { refrescar, setModo } from "./panel.js";
import { pintarRanking } from "./ranking.js";
import { cargarTipicas } from "./tipicas.js";
import { cargarDias, puedeTrayecto, vaciarTrayecto } from "./trayecto.js";
import { $, consulta, esc, fmt, pedirJson } from "./util.js";

const MIN_OBS = { sinFiltros: 30, conFiltros: 5, conBus: 1 };  // registros mínimos por celda según lo filtrado

// Cada lista respeta los otros filtros (ej. solo los recorridos del servicio elegido)
async function actualizarListas() {
  const res = await Promise.all(DIMENSIONES.map(g => pedirJson("/ranking" + consulta(estado.filtros, { grupo: g }))));
  DIMENSIONES.forEach((g, i) => {
    const filas = [...res[i]].sort((a, b) => String(a.id).localeCompare(String(b.id), "es", { numeric: true }));
    const opciones = filas.map(r => {
      const extra = `${fmt(r.buses, 0)} ${r.buses === 1 ? "bus" : "buses"} · ${fmt(r.vel_mov)} km/h`;
      const cod = g !== "sentido" ? tipoCodigo(r.id) : null;
      return `<option value="${esc(r.id)}" data-sec="${esc(extra)}"${cod ? ` data-chip="${cod}"` : ""}>` +
        `${esc(textoValor(g, r.id))}</option>`;
    });
    // Un valor que no está en la lista (enlace viejo o con errores) se muestra igual, marcado "sin datos"
    const v = estado.filtros[g];
    const falta = v && !filas.some(r => String(r.id) === v)
      ? `<option value="${esc(v)}" data-sec="sin datos">${esc(textoValor(g, v))}</option>` : "";
    $("f_" + g).innerHTML = `<option value="">Todos (${fmt(filas.length, 0)})</option>` + falta + opciones.join("");
    $("f_" + g).value = v || "";
  });
  datos.rankingFilas = res[DIMENSIONES.indexOf("recorrido")];
}

export async function aplicarFiltros({ encuadre = true } = {}) {
  cargando(true);
  ocultarError();
  const n = Object.keys(estado.filtros).length;
  // Antes de consultar: si la consulta falla (ej. un enlace con filtros que ya no existen), los filtros
  // igual se pueden quitar
  for (const d of DIMENSIONES) document.querySelector(`[data-limpiar="${d}"]`).hidden = !estado.filtros[d];
  $("limpiarTodo").disabled = !n;
  $("notaModos").hidden = !!n;  // por qué los modos están deshabilitados, a la vista
  try {
    const conBus = !!estado.filtros.patente;
    estado.minObs = conBus ? MIN_OBS.conBus : n ? MIN_OBS.conFiltros : MIN_OBS.sinFiltros;
    $("minObs").value = estado.minObs;
    // Trayecto: con bus siempre; sin bus (todos los buses de lo filtrado) solo en ese modo, porque pesa más
    const conTray = puedeTrayecto() && (conBus || estado.modo === "trayecto");
    const [porRec] = await Promise.all([
      n ? pedirJson("/por_recorrido" + consulta(estado.filtros)) : null,
      cargarCeldas(), actualizarListas(), conTray ? cargarDias() : null,
    ]);
    datos.porRec = porRec;
    if (!conTray) vaciarTrayecto();
    $("btnRecorrido").disabled = !n;
    $("btnTrayecto").disabled = !puedeTrayecto();
    if (!puedeTrayecto() && estado.modo === "trayecto") setModo(n ? "recorrido" : "calor", false);
    if (!n && estado.modo === "recorrido") setModo("calor", false);
    refrescar();
    pintarRanking();
    cargarTipicas();  // las trayectorias típicas llegan después, sin bloquear el resto
    if (encuadre) {
      if (estado.modo === "trayecto") encuadrarTrayecto();
      else if (n) encuadrarCeldas();
      else volverAVistaInicial();
    }
  } catch (e) {
    mostrarError(e, () => aplicarFiltros({ encuadre }));
  } finally {
    cargando(false);
  }
}

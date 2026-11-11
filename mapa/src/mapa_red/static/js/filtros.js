// Filtros: un pedido a /vista con todo lo que cambia con ellos (mapa, listas, ranking, recorridos, días) y
// el redibujo. Antes eran hasta 7 pedidos por cambio; en Lambda, cada uno podía ser un arranque en frío.
import { cargando, mostrarError, ocultarError } from "./avisos.js";
import { leerPaquete } from "./calor.js";
import { DIMENSIONES, textoValor, tipoCodigo } from "./dominio.js";
import { datos, estado } from "./estado.js";
import { encuadrarCeldas, encuadrarTrayecto, volverAVistaInicial } from "./mapa.js";
import { refrescar, setModo } from "./panel.js";
import { pintarRanking } from "./ranking.js";
import { pintarPanelReferencia } from "./referencia.js";
import { cargarTipicas } from "./tipicas.js";
import { cargarTrayecto, ponerDias, puedeTrayecto, vaciarTrayecto } from "./trayecto.js";
import { $, consulta, esc, fmt, pedir } from "./util.js";

const MIN_OBS = { sinFiltros: 30, conFiltros: 5, conBus: 1 };  // registros mínimos por celda según lo filtrado

// Mapa de calor + (en la cabecera) listas, ranking, recorridos y días. `v` = la caché vigente: mientras sea
// la misma, la respuesta no cambia y el navegador (o CloudFront) la puede guardar.
async function pedirVista(conDias) {
  const r = await pedir("/vista" + consulta(estado.filtros, { ...(conDias ? { dias: 1 } : {}), v: CONFIG.cache }));
  return leerPaquete(await r.arrayBuffer());
}

// Listas de los filtros: cada una respeta los otros filtros (ej. solo los recorridos del servicio elegido)
function ponerListas(listas) {
  DIMENSIONES.forEach(g => {
    const filas = [...listas[g]].sort((a, b) => String(a.id).localeCompare(String(b.id), "es", { numeric: true }));
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
  datos.rankingFilas = listas.recorrido;
}

// Cada aplicación tiene un número: si los filtros cambian mientras llega una respuesta, la vieja se descarta
// (si no, una respuesta lenta podía pisar a la más reciente)
let ultima = 0;

export async function aplicarFiltros({ encuadre = true } = {}) {
  const esta = ++ultima;
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
    // Trayecto: con bus siempre; sin bus (todos los buses de lo filtrado) solo en ese modo, porque pesa más
    const conTray = puedeTrayecto() && (conBus || estado.modo === "trayecto");
    const { cabecera, C } = await pedirVista(conTray);
    if (esta !== ultima) return;
    datos.META = cabecera; datos.C = C;  // juntos: nunca queda una cabecera de una respuesta con datos de otra
    datos.porRec = cabecera.por_recorrido;
    ponerListas(cabecera.listas);
    estado.minObs = conBus ? MIN_OBS.conBus : n ? MIN_OBS.conFiltros : MIN_OBS.sinFiltros;
    $("minObs").value = estado.minObs;
    if (conTray) {  // el trayecto es otro pedido: depende del día, que se elige con la lista de días
      ponerDias(cabecera.dias);
      await cargarTrayecto(() => esta === ultima);
      if (esta !== ultima) return;
    } else vaciarTrayecto();
    $("btnRecorrido").disabled = !n;
    $("btnTrayecto").disabled = !puedeTrayecto();
    if (!puedeTrayecto() && estado.modo === "trayecto") setModo(n ? "recorrido" : "calor", false);
    if (!n && estado.modo === "recorrido") setModo("calor", false);
    refrescar();
    pintarPanelReferencia($("refPanel"));  // cuántos trazados y paraderos del servicio filtrado
    pintarRanking();
    cargarTipicas();  // las trayectorias típicas llegan después, sin bloquear el resto
    if (encuadre) {
      if (estado.modo === "trayecto") encuadrarTrayecto();
      else if (n) encuadrarCeldas();
      else volverAVistaInicial();
    }
  } catch (e) {
    if (esta === ultima) mostrarError(e, () => aplicarFiltros({ encuadre }));
  } finally {
    cargando(false);
  }
}

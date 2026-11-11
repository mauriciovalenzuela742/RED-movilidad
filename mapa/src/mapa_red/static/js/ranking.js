// Ranking de recorridos: tabla ordenable por columna; clic en una fila filtra por ese recorrido.
import { colorRec } from "./capas.js";
import { COLOR_OTROS, colorPara } from "./colores.js";
import { ETIQUETA, MAX_FILAS_RANKING, marcaCodigo, textoValor } from "./dominio.js";
import { datos, estado, rankOrden, tray } from "./estado.js";
import { colorActual, colorRecorrido } from "./trayecto.js";
import { $, esc, fmt } from "./util.js";

const NOMBRE_COLUMNA = { id: "recorrido", n: "registros", vel_mov: "km/h en movimiento", vel_com: "km/h en ruta",
                         pct_det: "% detenido", buses: "buses" };

// El punto de cada fila lleva el color con que ese recorrido se dibuja en el mapa: el de su recorrido (o el
// gris de "Otros"), o el de su velocidad cuando el mapa colorea por velocidad. null = no se dibuja (punto
// vacío): nunca un color de velocidad en una lista de colores de recorrido, ni al revés.
function colorFila(r) {
  const porRecorrido = estado.modo === "recorrido" ? !!datos.porRec
    : estado.modo === "trayecto" && colorActual() === "recorrido";
  if (!porRecorrido) return colorPara(r.vel_mov, "vel_mov");
  if (estado.modo === "trayecto") return tray.recs.includes(r.id) ? colorRecorrido(r.id) : null;
  if (estado.filtros.recorrido && r.id !== estado.filtros.recorrido) return null;  // el mapa muestra solo el filtrado
  const i = datos.porRec.recorridos.findIndex(x => x.id === r.id);
  return i >= 0 ? colorRec(i) : COLOR_OTROS;  // fuera de los 5 con más registros: en "Otros"
}

const punto = c => c ? `<span class="punto" aria-hidden="true" style="background:rgb(${c})"></span>`
  : `<span class="punto sin-color" aria-hidden="true" title="No se dibuja en el mapa"></span>`;

function filasOrdenadas() {
  const { col, desc } = rankOrden, s = desc ? -1 : 1;
  return [...datos.rankingFilas].sort((a, b) => {
    const x = a[col], y = b[col];
    if (x == null || y == null) return (x == null) - (y == null);  // sin dato, siempre al final
    return s * (col === "id" ? String(x).localeCompare(String(y), "es", { numeric: true }) : x - y);
  });
}

// Clic en un encabezado ordena por esa columna; otro clic invierte el sentido. Los nombres parten de A a Z;
// los números, de mayor a menor.
export function ordenarPor(col) {
  rankOrden.desc = rankOrden.col === col ? !rankOrden.desc : col !== "id";
  rankOrden.col = col;
  pintarRanking();
}

export function pintarRanking() {
  const todas = filasOrdenadas(), filas = todas.slice(0, MAX_FILAS_RANKING);
  const sel = estado.filtros.recorrido;
  $("tabla").innerHTML = filas.map(r => `
    <tr data-id="${esc(r.id)}" class="${r.id === sel ? "sel" : ""}">
      <td><button type="button" class="fila-btn" aria-pressed="${r.id === sel}">${punto(colorFila(r))}${esc(r.id)}</button>${marcaCodigo(r.id)}</td>
      <td>${fmt(r.n, 0)}</td><td>${fmt(r.vel_mov)}</td><td>${r.vel_com != null ? fmt(r.vel_com) : "–"}</td>
      <td>${fmt(r.pct_det, 0)}</td><td>${fmt(r.buses, 0)}</td></tr>`).join("") ||
    `<tr><td colspan="6" class="nota vacio">Ningún recorrido con estos filtros.</td></tr>`;
  document.querySelectorAll("#panelRanking th[data-orden]").forEach(th => {
    const activa = th.dataset.orden === rankOrden.col;
    if (activa) th.setAttribute("aria-sort", rankOrden.desc ? "descending" : "ascending");
    else th.removeAttribute("aria-sort");
    th.querySelector(".ms").textContent = activa && !rankOrden.desc ? "arrow_upward" : "arrow_downward";
  });
  const sentido = rankOrden.col === "id" ? (rankOrden.desc ? "de Z a A" : "de A a Z")
    : (rankOrden.desc ? "de más a menos" : "de menos a más");
  $("ordenRanking").textContent = `Ordenados por ${NOMBRE_COLUMNA[rankOrden.col]}, ${sentido}.`;
  const otros = Object.entries(estado.filtros).filter(([k]) => k !== "recorrido")
    .map(([k, v]) => `${ETIQUETA[k].toLowerCase()} ${textoValor(k, v)}`);
  $("notaRanking").textContent = `${fmt(todas.length, 0)} recorrido${todas.length === 1 ? "" : "s"}` +
    (todas.length > MAX_FILAS_RANKING ? ` (se muestran los primeros ${MAX_FILAS_RANKING})` : "") +
    (otros.length ? ` · filtrado por ${otros.join(", ")}` : "") +
    " · todo el período, todas las horas. Elige una fila para verlo en el mapa.";
}

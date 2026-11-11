// Trayectorias típicas (un viaje real por recorrido): en Recorridos con cualquier filtro; en Calor con
// servicio o recorrido (la línea coloreada por velocidad). Se piden una vez por combinación de filtros y
// llegan después del resto, sin bloquearlo.
import { datos, estado, tipicas } from "./estado.js";
import { leyenda, refrescar } from "./panel.js";
import { consulta, pedirJson } from "./util.js";

export const usaTipicas = () => estado.modo === "trayecto" ? false
  : estado.modo === "recorrido" ? Object.keys(estado.filtros).length > 0
  : !!(estado.filtros.recorrido || estado.filtros.servicio);

export async function cargarTipicas() {
  if (!usaTipicas()) return;
  const clave = consulta(estado.filtros);
  if (tipicas.clave === clave) return;
  tipicas.clave = clave; tipicas.datos = null; tipicas.cargando = true;
  if (datos.C) leyenda();
  try {
    const filas = await pedirJson("/tipicas" + clave).catch(() => []);  // sin típica, el mapa sigue igual
    if (tipicas.clave === clave) tipicas.datos = filas;  // descartar si los filtros cambiaron entretanto
  } finally {
    tipicas.cargando = false;
    if (datos.C) refrescar();
  }
}

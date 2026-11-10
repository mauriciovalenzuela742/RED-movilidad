// Avisos: carga (contador, por si hay varias consultas a la vez), error con reintentar y botones de
// reproducción (ícono y nombre accesible según el estado).
import { $ } from "./util.js";

let cargas = 0;
export function cargando(si) {
  cargas = Math.max(0, cargas + (si ? 1 : -1));
  $("cargando").hidden = cargas === 0;
}

export async function conCarga(fn) {
  cargando(true);
  try { return await fn(); } finally { cargando(false); }
}

export function mostrarError(e, reintentar) {
  const msg = String(e?.message || e || "").replace(/<[^>]*>/g, "").trim();
  $("errorTxt").textContent = "No se pudo consultar" + (msg ? `: ${msg.length > 240 ? msg.slice(0, 240) + "…" : msg}` : ".");
  $("errorReintentar").hidden = !reintentar;
  $("errorReintentar").onclick = () => { ocultarError(); reintentar(); };
  $("error").hidden = false;
}
export const ocultarError = () => { $("error").hidden = true; };

// Cerrar el aviso de error: su botón o Escape
export function activarAvisos() {
  $("errorCerrar").onclick = ocultarError;
  document.addEventListener("keydown", e => { if (e.key === "Escape") ocultarError(); });
}

// Cambia solo el texto del ícono y solo si cambió el estado. Antes se reescribía el HTML del botón en cada
// cuadro de la reproducción: si el ícono se reemplazaba entre el mousedown y el mouseup, el navegador
// descartaba el clic y "Pausar" no funcionaba (se reprodujo: 6 de 8 clics perdidos).
export function iconoPlay(id, pausa) {
  const b = $(id);
  if (b.dataset.pausa === String(pausa)) return;
  b.dataset.pausa = String(pausa);
  const base = b.dataset.etiqueta || (b.dataset.etiqueta = b.getAttribute("aria-label"));
  b.querySelector(".ms").textContent = pausa ? "pause" : "play_arrow";
  b.setAttribute("aria-label", pausa ? "Pausar" : base);
  b.title = pausa ? "Pausar" : base;
}

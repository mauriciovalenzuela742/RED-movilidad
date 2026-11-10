// Estado en la URL: la vista se puede compartir, guardar o recargar tal cual.
// Ej.: ?recorrido=T301 00R&patente=BJFV-17&modo=trayecto&dia=2025-09-17
import { METRICAS } from "./colores.js";
import { DIMENSIONES } from "./dominio.js";
import { estado, tray } from "./estado.js";
import { setModo } from "./panel.js";
import { $ } from "./util.js";

const RETARDO_MS = 400;

// Con retardo: refrescar() corre en cada cuadro de la animación y al arrastrar la hora, y los navegadores
// limitan history.replaceState (Safari lanza error sobre 100 llamadas en 30 s)
let timer = null;
export function guardarUrl() {
  clearTimeout(timer);
  timer = setTimeout(escribirUrl, RETARDO_MS);
}

function escribirUrl() {
  const p = new URLSearchParams(estado.filtros);
  if (estado.modo !== "calor") p.set("modo", estado.modo);
  if (estado.metrica !== "vel_mov") p.set("metrica", estado.metrica);
  if (estado.tipoDia !== "todos") p.set("tipo_dia", estado.tipoDia);
  if (!estado.todoDia) p.set("hora", estado.hora);
  if (estado.modo === "trayecto" && tray.dia) p.set("dia", tray.dia);
  if (estado.modo === "trayecto" && tray.colorPor) p.set("color", tray.colorPor);
  const q = p.toString(), url = location.pathname + (q ? "?" + q : "");
  try {
    if (url !== location.pathname + location.search) history.replaceState(null, "", url);
  } catch { /* la URL es una comodidad: si el navegador la limita, la app sigue igual */ }
}

// Aplica al estado (y a los controles) lo que venga en la URL; lo inválido se ignora
export function leerUrl() {
  const p = new URLSearchParams(location.search);
  for (const d of DIMENSIONES) if (p.get(d)) estado.filtros[d] = p.get(d);
  if (METRICAS[p.get("metrica")]) { estado.metrica = p.get("metrica"); $("metrica").value = estado.metrica; }
  if (["0", "1"].includes(p.get("tipo_dia"))) { estado.tipoDia = p.get("tipo_dia"); $("tipoDia").value = estado.tipoDia; }
  const h = parseInt(p.get("hora"), 10);
  if (h >= 0 && h <= 23) {
    estado.hora = h; estado.todoDia = false;
    $("hora").value = h; $("hora").disabled = false; $("todoDia").checked = false;
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(p.get("dia") || "")) tray.dia = p.get("dia");
  if (["velocidad", "recorrido"].includes(p.get("color"))) tray.colorPor = p.get("color");
  if (["recorrido", "trayecto"].includes(p.get("modo"))) setModo(p.get("modo"), false);
}

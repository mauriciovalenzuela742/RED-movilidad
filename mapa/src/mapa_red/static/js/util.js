// Utilidades sin estado: DOM, formato y HTTP.

export const $ = id => document.getElementById(id);
export const fmt = (x, n = 1) => Number(x).toLocaleString("es-CL", { maximumFractionDigits: n });
export const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");

// Segundos desde la medianoche del día operativo → "HH:MM". Después de medianoche los segundos siguen
// (> 86 400) y se muestran como hora del reloj (00:30)
export const hhmm = s =>
  `${String(Math.floor(s / 3600) % 24).padStart(2, "0")}:${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}`;

// Con "reducir movimiento" en el sistema, el mapa salta al encuadre en vez de volar
export const DURACION_MAPA = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 800;

export function consulta(filtros, extra = {}) {
  const p = new URLSearchParams({ ...filtros, ...extra });
  return p.toString() ? "?" + p : "";
}

// Respuesta HTTP con error → Error con el mensaje para el usuario: el `detail` de FastAPI
// ({"detail": "…"}) si viene, si no el texto de la respuesta o el código HTTP
async function errorHttp(r) {
  const texto = await r.text();
  let msg = texto;
  try {
    const d = JSON.parse(texto).detail;
    if (d) msg = typeof d === "string" ? d : JSON.stringify(d);
  } catch { /* no era JSON: queda el texto */ }
  return new Error(msg.trim() || `HTTP ${r.status}`);
}

export async function pedir(url) {
  const r = await fetch(url);
  if (!r.ok) throw await errorHttp(r);
  return r;
}
export const pedirJson = async url => (await pedir(url)).json();

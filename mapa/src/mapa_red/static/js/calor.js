// Mapa de calor: matrices por celda que manda el servidor y su suma según tipo de día y hora.
import { datos, estado } from "./estado.js";
import { consulta, pedir } from "./util.js";

// Los mismos tipos que consultas.TIPOS_BINARIOS en el servidor (los conteos van como uint16 o, si no caben, uint32)
const TIPOS = { float32: Float32Array, uint32: Uint32Array, uint16: Uint16Array, uint8: Uint8Array };

// Celdas del mapa de calor: [uint32 largo][JSON meta][arreglos]. Las matrices tienen forma
// [tipo de día (2), hora (24), celda], así que la hora se elige y se anima aquí, sin volver al servidor.
export async function cargarCeldas() {
  const r = await pedir("/celdas" + consulta(estado.filtros));
  const buf = await r.arrayBuffer();
  const largo = new DataView(buf).getUint32(0, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, largo)));
  const base = 4 + largo, C = {};
  for (const [k, v] of Object.entries(meta.indice)) C[k] = new TIPOS[v.tipo](buf, base + v.offset, v.len);
  datos.META = meta; datos.C = C;  // juntos: nunca queda una cabecera de una respuesta con datos de otra
}

// Suma las matrices según el tipo de día y la hora elegidos y arma las celdas visibles, más los totales
export function calcular() {
  const { C, META } = datos;
  const nc = META.n_celdas;
  const tipos = estado.tipoDia === "todos" ? [0, 1] : [Number(estado.tipoDia)];
  const horas = estado.todoDia ? [...Array(24).keys()] : [estado.hora];
  const nt = new Float64Array(nc), nm = new Float64Array(nc), sv = new Float64Array(nc);
  const sc = new Float64Array(nc), vc = new Float64Array(nc);  // vel. comercial ponderada por tiempo en ruta
  for (const t of tipos) for (const h of horas) {
    const base = (t * 24 + h) * nc;
    for (let c = 0; c < nc; c++) {
      const tot = C.n_total[base + c];
      if (!tot) continue;
      const mov = C.n_mov[base + c];
      nt[c] += tot; nm[c] += mov; sv[c] += mov * C.vel[base + c] / 2;
      const s = C.scom[base + c]; sc[c] += s; vc[c] += s * C.vcom[base + c] / 2;
    }
  }
  const celdas = [];
  let T = 0, M = 0, S = 0, SC = 0, VC = 0;
  for (let c = 0; c < nc; c++) {
    T += nt[c]; M += nm[c]; S += sv[c]; SC += sc[c]; VC += vc[c];
    if (nt[c] < estado.minObs) continue;
    const d = { pos: [C.lon[c], C.lat[c]], n: nt[c], vel_mov: nm[c] ? sv[c] / nm[c] : 0,
                vel_efec: sv[c] / nt[c], pct_det: 100 * (1 - nm[c] / nt[c]),
                vel_com: sc[c] >= 12 ? vc[c] / sc[c] : null };  // ≥ 2 min en ruta en la celda
    if (estado.metrica === "vel_com" && d.vel_com == null) continue;
    d.valor = d[estado.metrica];
    celdas.push(d);
  }
  return { celdas, total: { n: T, vel_mov: M ? S / M : 0, pct_det: T ? 100 * (1 - M / T) : 0,
                            vel_com: SC ? VC / SC : null } };
}

// Conceptos del dominio: filtros, nombres y códigos de recorrido.

// Mismas dimensiones que `esquema.DIMENSIONES` en el servidor
export const DIMENSIONES = ["servicio", "sentido", "recorrido", "patente"];
export const ETIQUETA = { servicio: "Servicio", sentido: "Sentido", recorrido: "Recorrido", patente: "Bus" };
const NOMBRE_SENTIDO = { I: "Ida", R: "Regreso", "-": "Sin sentido" };
export const textoValor = (dim, v) => dim === "sentido" ? (NOMBRE_SENTIDO[v] || v) : v;

export const MAX_FILAS_RANKING = 300;
export const VISTA_INICIAL = { center: [-70.65, -33.46], zoom: 10.6 };
export const METROS_POR_GRADO = 111320;

// "TS" = fuera de servicio; "9X" = 9 en la penúltima cifra (posible no comercial, por confirmar con DTPM)
export const TITULO_CODIGO = { TS: "Fuera de servicio", "9X": "Posible servicio no comercial" };
export function tipoCodigo(id) {
  const s = String(id ?? "").split(" ")[0];
  if (/^T\d+TS$/.test(s)) return "TS";
  if (/^T\d*9\d$/.test(s)) return "9X";
  return null;
}
// Recorridos que no son servicio comercial: códigos TS y registros sin recorrido asignado
export const fueraDeServicio = id => tipoCodigo(id) === "TS" || id === "(sin recorrido)";
export const marcaCodigo = id => {
  const c = tipoCodigo(id);
  return c ? `<span class="cb-chip" title="${TITULO_CODIGO[c]}">${c}<span class="sr"> (${TITULO_CODIGO[c]})</span></span>` : "";
};

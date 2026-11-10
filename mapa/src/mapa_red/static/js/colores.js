// Colores de los datos: escala de velocidad (divergente) y paleta categórica (recorridos).
//
// Validadas con el validador de la skill de visualización (OKLab, simulación de daltonismo Machado 2009)
// contra el mapa base (#f5f5f4). En un mapa cualquier color puede quedar junto a cualquier otro, así que la
// paleta se valida con todos los pares (--pairs all): con más de 5 colores siempre hay dos que se confunden
// (ej. naranja y rojo, incluso con visión normal). Por eso hay 5 y el resto va a "Otros" en gris; nunca se
// reciclan colores. Los 5 son de la paleta de referencia de la skill; la anterior (12 tonos de la marca RED)
// fallaba la validación: grises y negro que se leen como gris, y tres tonos casi invisibles sobre el mapa.

// Velocidad: rampa divergente con los colores de RED, rojo = lento → gris (≈ 25 km/h, lo típico) → azul = fluido
export const RAMPA = [[207, 21, 45], [231, 138, 150], [191, 189, 189], [134, 174, 213], [13, 93, 171]];

export const METRICAS = {
  vel_mov:  { titulo: "km/h", dominio: [10, 40], invertir: false },
  vel_efec: { titulo: "km/h", dominio: [0, 30], invertir: false },
  vel_com:  { titulo: "km/h comercial", dominio: [8, 34], invertir: false },
  pct_det:  { titulo: "% detenido", dominio: [10, 80], invertir: true },
};

// Recorridos: orden fijo (el color sigue al recorrido, no a su posición en una lista que cambia). Azul y rojo
// primero: el caso más común es una ida y su regreso. Peor par: daltonismo ΔE 6,9 (legal con leyenda y
// tooltip, que siempre están), visión normal ΔE 15,6. Amarillo y aqua tienen contraste < 3:1 con el mapa:
// las líneas llevan un borde oscuro.
export const PALETA = [[42, 120, 214], [227, 73, 72], [0, 131, 0], [237, 161, 0], [27, 175, 122]];
export const COLOR_OTROS = [143, 141, 136];   // #8f8d88: el resto y "fuera de servicio" (3,0:1 con el mapa)
export const COLOR_SIN_DATOS = [143, 141, 136];
export const COLOR_BORDE = [72, 72, 70];      // borde de las líneas: las claras se ven sobre el mapa claro

// Color de un valor de la métrica `m` en la rampa
export function colorPara(v, m) {
  const { dominio, invertir } = METRICAS[m];
  let t = (v - dominio[0]) / (dominio[1] - dominio[0]);
  t = Math.min(1, Math.max(0, invertir ? 1 - t : t));
  const x = t * (RAMPA.length - 1), i = Math.min(Math.floor(x), RAMPA.length - 2), f = x - i;
  const a = RAMPA[i], b = RAMPA[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

// Asigna colores a recorridos ordenados por importancia (ej. registros): los primeros 5 en servicio llevan
// un color de la paleta; el resto y los "fuera de servicio" (TS, sin recorrido), gris
export function coloresPorRecorrido(recorridos, esFueraDeServicio) {
  const colores = new Map();
  let i = 0;
  for (const r of recorridos) colores.set(r, !esFueraDeServicio(r) && i < PALETA.length ? PALETA[i++] : COLOR_OTROS);
  return colores;
}

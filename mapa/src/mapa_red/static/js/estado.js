// Estado compartido de la página. Son objetos mutables (no variables sueltas) para que todos los módulos
// lean y escriban el mismo estado: un `export let` no se puede reasignar desde otro módulo.

// Lo que elige el usuario (y se guarda en la URL)
export const estado = {
  modo: "calor",       // calor | recorrido | trayecto
  metrica: "vel_mov", tipoDia: "todos", hora: 8, todoDia: true,
  minObs: 30, radio: 18,
  filtros: {},         // servicio, sentido, recorrido, patente
};

// Lo que responde el servidor
export const datos = {
  C: null, META: null,  // matrices del mapa de calor (calor.js) y su cabecera
  porRec: null,         // celdas y leyenda de la vista Recorridos
  rankingFilas: [],     // recorridos con sus métricas (ranking y lista del filtro)
  ultimasCeldas: [],    // celdas dibujadas en el último refresco (para encuadrar)
};

// Mapa y capa de deck.gl (mapa.js)
export const vista = { mapa: null, overlay: null };

// Trayecto. Con bus: su día (o la parte filtrada). Sin bus, con servicio o recorrido filtrado: todos los
// buses que lo hicieron ese día ("varios"). Lo que se dibuja se arma una vez al cargar (trayecto.js).
export const tray = {
  dias: [], dia: null, varios: false,
  pts: [], viajes: [], caminos: [], tramos: [],     // puntos, viajes (puntos por viaje), caminos y tramos a dibujar
  caminosPorBus: new Map(), tramosPorBus: new Map(),
  recs: [], colores: new Map(), puntosPorRec: new Map(),  // recorridos de más a menos puntos y su color
  buses: [], puntosPorBus: new Map(),
  version: 0,             // cambia con cada carga: deck.gl recalcula los colores
  tiempo: null,           // hora de la reproducción (s desde la medianoche); null = día completo
  timer: null,            // cuadro pendiente de la reproducción (requestAnimationFrame)
  colorPor: null,         // "velocidad" | "recorrido"; null = el de cada modo (trayecto.colorActual)
  resaltado: null,        // patente resaltada (mouse sobre el bus o su nombre)
  etiquetas: true,        // patente sobre cada bus en marcha durante la reproducción
};

// Trayectorias típicas (un viaje real por recorrido), pedidas una vez por combinación de filtros
export const tipicas = { clave: null, datos: null, cargando: false };

// Orden del ranking: por defecto, más registros primero
export const rankOrden = { col: "n", desc: true };

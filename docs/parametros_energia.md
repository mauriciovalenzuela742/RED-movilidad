# Parámetros del modelo de energía y cifras de referencia (PulsoRED)

Fecha de la investigación: 14-oct-2026. Hecha por búsqueda web y lectura de las fuentes.

**Convención de marcas.** `[LEÍDO]` = el valor está en el texto de la fuente que abrí. `[SECUNDARIO]` = lo cita otra fuente o una nota de prensa y no vi el original. `[INFERENCIA]` = lo calculé o lo deduje yo. `[NO LEÍDO]` = conozco el título por una búsqueda pero no pude abrir el texto; no se usa en ningún valor recomendado.

**Aviso de alcance.** No encontré ningún modelo publicado, ajustado con datos reales y con coeficientes abiertos, que use a la vez velocidad media, detenciones por km y pendiente para **camiones** eléctricos. Lo más cercano es: (a) una regresión de camiones reales con velocidad, peso, temperatura y desnivel (Öko-Institut 2025), y (b) una regresión de buses simulados con pendiente, velocidad y detenciones por km (Abdelaty y Mohamed 2021). Por eso la recomendación es un modelo físico mesoscópico con parámetros citados, comparado contra esas dos curvas empíricas (sección 1.4).

---

## 1. Modelo mesoscópico de consumo

### 1.1 Modelo A: regresión de camiones eléctricos reales (el más cercano a camión)

- **Fuente:** Le Corguillé, Hacker, Göckeler, Mottschall, Dolinga (Oeko-Institut e.V.), *Real-world data analysis of energy consumption, activity and charging patterns of battery electric trucks operating in Germany* (2º informe del proyecto ELV LIVE, FKZ 16EM6003-1), Berlín, julio 2025. https://www.oeko.de/fileadmin/oekodoc/Real-world-data-analysis-of-battery-electric-trucks.pdf
- **Datos:** 19 camiones Mercedes eActros 300 y 400 en logística regional, Alemania, sept-2023 a ene-2025. Más de 800 eventos de conducción (la regresión usa N = 5.431 filas), con kWh medidos en el vehículo (sin pérdidas de carga) desde la telemática Fleetboard. Peso bruto de 11 a 40 t; la comparación con el fabricante usa 11 a 18 t. La velocidad no se mide en el camión: se calcula por evento como distancia / tiempo. `[LEÍDO]`
- **Forma funcional** (ecuación 2 del informe) `[LEÍDO]`:

  `C = m1·exp(-k1·s) + m2·t + m3·w + m4·a + m5`  [kWh/km]

  s = velocidad media del evento (km/h); t = temperatura exterior (°C); w = peso bruto del evento (t); a = diferencia de altitud entre inicio y fin del evento (m).

| Coeficiente | Valor | Unidad | Nota |
|---|---|---|---|
| m1 | 5,1304 | kWh/km | error estándar 0,076 |
| k1 | 0,17 | 1/(km/h) | fijado al ajustar la curva sin camiones con unidad de refrigeración |
| m2 | -0,0132 | kWh/km por °C | |
| m3 | 0,0183 | kWh/km por t | |
| m4 | 0,0015 | kWh/km por m de desnivel neto | no es pendiente; ver límites |
| m5 | 0,7091 | kWh/km | |

  R² = 0,474 (informe dice 0,463 en un párrafo y 0,474 en la tabla). Camiones con unidad de refrigeración (TCU): +0,092 kWh/km adicionales.
- **Límites que dicen los propios autores** `[LEÍDO]`: la muestra son 19 camiones del mismo modelo, no es representativa; la velocidad media está fuertemente correlacionada con el largo y tipo de viaje (los viajes lentos son cortos y con arranques), así que el efecto de la velocidad mezcla "velocidad" con "partidas"; la curva es fiable de 20 a 90 km/h y entre 0 y 20 km/h hay muchos valores atípicos; el desnivel es neto del evento, no el perfil de pendiente.
- **Valores que resultan** `[INFERENCIA]` (mi cálculo con la ecuación, w = 16 t, t = 20 °C, a = 0, es decir `C = 5,1304·exp(-0,17·s) + 0,738`):

| s (km/h) | 10 | 15 | 20 | 30 | 40 | 50 |
|---|---|---|---|---|---|---|
| C (kWh/km) | 1,68 | 1,14 | 0,91 | 0,77 | 0,74 | 0,74 |

  Comprobación `[LEÍDO]`: el informe dice que pasar de 20 a 30 km/h baja el consumo 0,138 kWh/km; la fórmula da 0,139.
- **Uso para el prototipo:** es una curva de contraste ("con qué velocidad media se parece mi modelo físico"), no el modelo principal, porque no incluye detenciones por km ni pendiente.

### 1.2 Modelo B: regresión de buses eléctricos (incluye pendiente, velocidad y detenciones/km)

- **Fuente:** Abdelaty, H. y Mohamed, M., *A Prediction Model for Battery Electric Bus Energy Consumption in Transit*, Energies 14(10):2824, 2021. https://www.mdpi.com/1996-1073/14/10/2824
- **Datos:** no son mediciones. Simulación en Simulink de un New Flyer XE40 (40 ft, batería 200 kWh) validada con los ensayos de Altoona (error ±3,81 %, y 6 % contra Autonomie). Diseño factorial completo de 907.199 escenarios a partir de 120 ciclos de conducción reales; la regresión múltiple explica R² = 0,961 (RMSE 0,3167 kWh/km); se validó con un segundo conjunto de 169.344 escenarios. Consumo medio del conjunto 1,654 kWh/km (rango -2,49 a 6,12). `[LEÍDO]`
- **Forma:** regresión lineal multivariable `EC = f(pendiente, estado de carga inicial, condición de camino, HVAC, pasajeros, agresividad, densidad de paradas, velocidad media)`. La ecuación 19 con la constante no se pudo recuperar del texto (es una imagen en la página). Sí leí los efectos marginales `[LEÍDO]`:

| Variable | Efecto sobre el consumo |
|---|---|
| Pendiente | +0,380 kWh/km por cada +1 % |
| Densidad de paradas | +0,128 kWh/km por cada +1 parada/km |
| Velocidad media | +0,07 kWh/km por cada +10 km/h (signo positivo; ver sección 3) |
| Pasajeros | +0,05 kWh/km por cada +10 pasajeros |
| HVAC | +0,036 kWh/km por cada +1 kW |
| Estado de carga inicial | +0,124 kWh/km por cada +10 % |
| Agresividad de manejo | +0,065 kWh/km por nivel |
| Condición del camino | +0,260 kWh/km por nivel |

  Rango de las densidades de paradas en el conjunto: 1,65 a 3,49 paradas/km. Escenarios de ruta: de 1,109 a 3,055 kWh/km.
- **Límites:** es un bus de 40 ft norteamericano, simulado, con ciclos construidos que separan "velocidad" de "paradas"; extrapolar a camiones no está respaldado. Sirve para decir cuánto pesa cada factor en orden de magnitud, no para predecir kWh de un camión.

### 1.3 Modelo C: físico de bajo muestreo, calibrado con buses de Santiago

- **Fuente:** Basso, Feijoo, Pezoa, Varas, Vidal, *The impact of electromobility in public transport: An estimation of energy consumption using disaggregated data in Santiago, Chile*, Energy 286 (2024) 129550. https://www.sciencedirect.com/science/article/pii/S0360544223029444 (texto completo leído desde una copia abierta).
- **Datos:** GPS de todos los buses de RED el 22-sep-2021 (DTPM) + mediciones de batería de 6 buses eléctricos sensorizados (TrackTec). Es el único trabajo que encontré con medición propia en Santiago. `[LEÍDO]`
- **Resultado que importa para el pitch** `[LEÍDO]`: error porcentual medio por intervalo de 15 min, en 2 buses no usados para calibrar: valor teórico del fabricante 104 %; **modelo físico sin calibrar 43 %**; SVR lineal calibrado con telemetría 22 %. La conclusión práctica es que un modelo físico con parámetros de literatura, sin calibrar, tiene errores grandes en intervalos cortos; los errores bajan al agregar por viaje o por día (otros estudios que cita: 7 % y 14 % por viaje).
- **Formulación física que usa (ecuaciones 1 a 4) y sus parámetros:** ver sección 1.5. Variables predictoras relevantes según Boruta: velocidad, RPM, ángulo de pendiente, uso de pedales, velocidad media, pendiente media, masa total, temperatura interior y del aire acondicionado.
- Otros candidatos que **no pude leer completos** (bloqueados): Hjelkrem et al. 2021, *A battery electric bus energy consumption model for strategic purposes*, Transp. Res. Part D 94:102804 (modelo de dinámica longitudinal con datos de baja frecuencia, 3.266 viajes en China y Noruega, con submodelo de auxiliares); Gallet, Massier, Hamacher 2018, *Estimation of the energy demand of electric buses based on real-world data...*, Applied Energy 230:344-356; Fiori et al. 2021 (Transp. Res. Part D), modelo micro validado contra una función macro velocidad-consumo con error de 6 %. `[NO LEÍDO]` Tienen el enfoque correcto (datos de baja frecuencia), pero no puedo dar coeficientes.

### 1.4 Forma recomendada para el prototipo `[INFERENCIA]`

Modelo **físico por tramo** (el tramo de ~100 m del atlas) con tres correcciones mesoscópicas, y **dos curvas empíricas de contraste**:

```
E_tramo [kWh] = [ E_rodadura + E_pendiente + E_aero(v) ] / η_tr          (tracción, solo si el término es > 0)
              + n_det · ½·m_ef·v_c² · ( 1/η_tr − η_reg )               (energía cinética neta por detención)
              + P_aux · (d / v_med)                                     (auxiliares, escalan con el tiempo)
con  E_rodadura = Crr·m·g·cos(θ)·d ;  E_pendiente = m·g·sin(θ)·d ;  E_aero = ½·ρ·Cd·A·v_ef²·d
```

- `d` longitud del tramo; `v_med` velocidad mediana del atlas; `n_det` = detenciones por km × d (del indicador C4 del plan); `v_c` velocidad de crucero antes de frenar (P85 del atlas); `m_ef = m·(1 + δ)` con δ factor rotacional (Basso usa 1,0 como factor de inercia de rotación).
- `v_ef`: con muestreo de 30–60 s, la velocidad media subestima el término aerodinámico porque la media de v³ es mayor que el cubo de la media. Usar `v_ef² ≈ v_med² + σ_v²`, con σ_v tomada del rango P15–P85 del atlas. Es una corrección mía; no la vi en ninguna fuente.
- Los auxiliares entran como potencia constante, por eso su costo por km crece al caer la velocidad: P_aux/v_med. Con 5 kW: 0,50 kWh/km a 10 km/h, 0,25 a 20 y 0,125 a 40 (aritmética, `[INFERENCIA]`).
- **Por qué esta forma y no una regresión:** (1) es la única que acepta pendiente del DEM, masa y detenciones por km a la vez sin un conjunto de entrenamiento de camiones, que no existe en abierto; (2) cada término tiene un parámetro con fuente que el jurado puede revisar; (3) la comparación de Basso muestra que sin calibrar falla (43 %), así que hay que **calibrar dos o tres escalares** (Cd·A, Crr y P_aux) contra un dato conocido: el consumo certificado o el rango de ensayo del fabricante (sección 2), y la curva de Öko para velocidades de 20 a 60 km/h. Si CASE entrega telemetría, se reemplaza esa calibración.
- **Contraste:** Modelo A (Öko) como curva de camiones reales; Modelo B (Abdelaty) para la sensibilidad a detenciones/km y pendiente. Si mi modelo físico se aparta mucho de A en 20–60 km/h con un camión de 16 t, algo está mal calibrado.

### 1.5 Formulación de dinámica longitudinal y valores típicos

Potencia en la batería, por instante o por tramo `[LEÍDO]` (Basso 2024, ecuaciones 2 a 4):

`P_t = v·(F_rr + F_ad + F_rg + F_acc) / (η_mo·η_tr)`, con `F_rr = Cr·m·g·cosα`, `F_ad = ½·ρ·Cd·Af·v²`, `F_rg = m·g·sinα`, `F_acc = m·δ·dv/dt`.
Si la suma de fuerzas es negativa, la batería recibe `-k_t·P_t`, con `k_t = 0,5·v/5` para `v < 5 m/s` y `k_t = 0,5 + 0,3·(v-5)/20` para `v ≥ 5 m/s` (de Zhang y Yao; v en m/s). Esto da una fracción regenerada de 0,5 a 5 m/s y 0,65 a 25 m/s.

| Parámetro | Valor | Fuente | Nota |
|---|---|---|---|
| Eficiencia motor+inversor η_mo | 0,85 | Basso 2024 Tabla 8 (cita a Asamer et al.) `[LEÍDO]` | bus eléctrico |
| Eficiencia transmisión η_tr | 0,97 | idem | tracción total 0,85·0,97 = 0,82 `[INFERENCIA]` |
| Crr bus | 0,01 | Basso 2024 (cita a Gao et al.) `[LEÍDO]` | |
| Crr bus y camión | 0,009 | Gao et al. 2019, Energy 172:823-839 (ORNL), Tabla 1 `[LEÍDO]` | rango en literatura 0,006 a 0,02 (Abdelaty) |
| Cd bus | 0,7 | Basso 2024; Gao 2019 `[LEÍDO]` | |
| Cd camión de reparto | 0,62 | Gao 2019 `[LEÍDO]` | camión de 15,4 t |
| Área frontal bus | 7,5 m² (diésel Euro VI) y 7,67 m² (eléctrico) | Basso 2024 Tabla 3, dato DTPM `[LEÍDO]` | masa de esa muestra 12.420 kg (eléctrico) y 19.204 kg (diésel) |
| Área frontal | 9,0 m² | Gao 2019, bus de 14,4 t y camión de 15,4 t `[LEÍDO]` | no usar para un camión de 8,5 t |
| Auxiliares eléctricos constantes | bus 5,0 kW; camión 4,0 kW | Gao 2019 `[LEÍDO]` | sin HVAC específico |
| Auxiliares worst-case + HVAC aparte | 7 kW + HVAC | Abdelaty 2021 (cita a Vepsäläinen) `[LEÍDO]` | |
| HVAC en Santiago (bus 18 m) | 0,2 kWh/km | ICCT/CMS 2024 `[LEÍDO]` | supuesto de ese estudio, no medición |
| Efecto del aire acondicionado medido | +9,3 % (Buenos Aires, máximo mensual) a +21 % (Montevideo) | ICCT São Paulo 2023, Tabla 1 `[SECUNDARIO]` | |
| Densidad del aire | 1,16 kg/m³ | Basso 2024 `[LEÍDO]` | Santiago está a ~520 m `[INFERENCIA]` |
| Factor de inercia rotacional δ | 1 | Basso 2024 `[LEÍDO]` | |
| Fracción regenerada k_t | 0,5 a 0,65 según velocidad | Basso 2024 (Zhang y Yao) `[LEÍDO]` | |
| Regeneración, buses reales | recupera 37–40 % de la energía requerida | *Characterization of battery electric transit bus energy consumption by temporal and speed variation*, Energy 2022 `[NO LEÍDO]`, solo resumen en buscador | |

Con la regeneración como energía neta por detención: `½·m_ef·v_c²·(1/η_tr − η_reg)`, donde η_reg es la fracción de la energía cinética que vuelve a la batería (usar 0,5 a 0,65 como rango y reportarlo como sensibilidad). Ejemplo `[INFERENCIA]` para un bus de 14 t: frenando desde 40 km/h (11,1 m/s) la energía cinética es 0,86 MJ = 0,24 kWh, y con tracción 0,82 y η_reg 0,5 a 0,65 el costo neto es 0,14 a 0,17 kWh por detención (0,55 a 0,69 kWh/km a 4 detenciones/km); desde 25 km/h son 0,05 a 0,07 kWh por detención (0,2 a 0,26 kWh/km). Es una cota superior: en tráfico real muchas detenciones son parciales, y por eso `v_c` debe salir del atlas y no fijarse.

---

## 2. Parámetros de vehículo

Tres vehículos de referencia. Todos los valores de la tabla son `[LEÍDO]` salvo los marcados.

| Parámetro | Reparto urbano 7,5–12 t: **Fuso eCanter 9C18e** | Rígido pesado 16–26 t: **Mercedes-Benz eEconic / eActros 300 (4x2 19 t y 6x2/4 27 t)** | Bus urbano 12 m: **BYD K9FE (Santiago desde 2017–18)** |
|---|---|---|---|
| Presencia en Chile | Presentado a clientes en Chile el 17-ago-2023, venta prevista para fines de 2023 (5 variantes de 6,5 a 8,55 t; 3 tamaños de batería; hasta 200 km) | Se vende en Chile el eActros 600 (tracto, primera unidad entregada 28-ene-2026). eActros 300 en Chile: **no pude confirmar** | Operando en RED desde 2017–18 (más de 2.000 buses de 12 m al 2023) |
| Peso bruto | 8.550 kg | 19 t (4x2) / 27 t (6x2/4) | 12,9 m BYD en São Paulo: 16.675 kg con media carga |
| Peso en vacío | 4.055 kg (batería L, 124 kWh); 3.550 a 3.605 kg (batería M, 82 kWh) | 8,1 t (4x2) / 9,2 t (6x2/4) (eEconic) | 14.750 a 14.937 kg (BYD 12,9 m, São Paulo); muestra DTPM de bus eléctrico: 12.420 kg `[el significado de "masa" en DTPM no está definido]` |
| Carga útil máxima | 4.495 kg (batería L); ~5.000 kg (M) (capacidad del chasis) | ~10,6 a 10,9 t (4x2); ~17,7 t (6x2/4), sin carrocería | 70 a 72 pasajeros (capacidad de diseño ~80) |
| Batería (utilizable / instalada) | 116 / 124 kWh (L); 78 / 82 kWh (M); LFP | 291 / 336 kWh (eActros 300, 3 paquetes). eActros 400: 388 / 448 kWh | 324 kWh nominal (BYD K9, Montevideo, Buenos Aires, Long Beach); utilizable no publicado |
| Área frontal | **Sin fuente** (ver sección 7) | 9,0 m² (Gao 2019, camión de 15,4 t, no del modelo) | 7,5 a 7,67 m² (DTPM vía Basso) |
| Cd | **Sin fuente** de este modelo; 0,62 (Gao, camión genérico) | 0,62 (Gao, camión genérico) | 0,7 (Basso, Gao) |
| Crr | 0,009 (Gao, genérico) | 0,009 (Gao, genérico) | 0,01 (Basso) |
| Eficiencia de tracción | **Sin fuente** del fabricante | **Sin fuente** del fabricante | 0,82 (0,85 × 0,97; Basso) |
| Regeneración | Sin dato publicado | Sin dato publicado | k_t 0,5 a 0,65 (Basso) |
| Auxiliares | Sin dato publicado | TCU (refrigeración) +0,092 kWh/km medido en eActros (Öko) | 5 kW base (Gao) + HVAC; HVAC 0,2 kWh/km (ICCT Santiago, supuesto) |
| Consumo real publicado | **No hay cifra del fabricante.** Derivado: 116 kWh / 200 km = 0,58 kWh/km (batería L); 78 / 140 = 0,56 (M). Condiciones de ese rango: carrocería tipo caja, 50 % de la carga útil, 20 °C `[INFERENCIA]` | **0,96 kWh/km** media real de 19 eActros 300/400, 11–18 t brutos, 19–21 °C, desnivel < 200 m (Öko 2025). Fabricante: 0,97 kWh/km = 291 kWh / 300 km, a 20 °C, carga parcial, sin remolque, 4x2 (Öko cita a Mercedes). Brasil: eActros 300, 0,97 kWh/km, carga neta media 21 t, 13,5 mil km (nota autodata, abr-2026) | Santiago, 2 BYD K9FE de 12 m, nov-2017 a oct-2018: **1,006 kWh/km** (Galarza 2020, citado por ICCT `[SECUNDARIO]`). São Paulo, BYD 12,9 m, 2 buses, velocidad media 13,6 a 14,1 km/h: 1,19 y 1,27 kWh/km (rango diario 0,94 a 2,29). Prueba SORT (HVAC apagado, media carga, 16,7 t): 0,935 (urbano), 0,859 (mixto), 0,859 (suburbano) kWh/km. Santiago, bus articulado de 18 m: 1,34 kWh/km medio en 335 días (rango 0,66 a 2,52), velocidad media ~12 km/h |

Fuentes de la tabla:
- Fuso: *eCanter Technical Datasheets 2024 EN 855T* (Mitsubishi Fuso Truck Europe), https://www.fuso-trucks.com/wp-content/uploads/sites/2/2024/05/eCanter_Technical-Datasheets_2024_EN_855T.pdf ; presentación en Chile: electrive, 22-ago-2023, https://www.electrive.com/es/2023/08/22/fuso-traera-el-ecanter-a-chile/ . La hoja es la versión europea; **la ficha chilena no la encontré** y la distribuidora no figura en lo que leí. Número de variantes (5) y de baterías (3) vienen de electrive.
- Mercedes eEconic: https://www.mercedes-benz-trucks.com/int/en/trucks/eeconic.html (336 kWh instalados, 330 kW continuos / 400 kW pico, carga máxima 160 kW; 20 a 80 % en ~75 min). eActros 300 utilizable 291 kWh: Öko 2025 citando al fabricante, e InsideEVs (2022) https://insideevs.com/news/611622/mercedes-eactros-300-tractor/ . eActros 300 en Brasil: https://www.autodata.com.br/noticias/2026/04/24/mercedes-benz-eactros-300-alcanca-autonomia-de-250-quilometros-em-testes-no-brasil/102593/ . eActros 600 en Chile: nota de prensa de La Tercera/mercado.com.ar `[SECUNDARIO]`.
- Bus: ICCT, *Operational analysis of battery electric buses in São Paulo* (feb-2023) https://theicct.org/wp-content/uploads/2023/02/Operational-analyis-of-battery-electric-buses-in-Sao-Paulo-final-feb2023.pdf ; ICCT/CMS para DTPM, *Simulación de la operación de un bus eléctrico de 18 metros en rutas de Santiago de Chile* (sept-2024) https://theicct.org/wp-content/uploads/2024/08/ID-59-%E2%80%93-Simulacion-Santiago_final.pdf ; Basso 2024.
- Otros modelos que operan en Santiago `[SECUNDARIO]` (electrive): Yutong de 9 y 12 m (214 buses a Voy Santiago, 2024; la batería de 422 kWh es una conjetura de electrive y no confirmada); Foton (pedido de 1.022 buses); BYD. No encontré hoja técnica de Foton U12 ni de Yutong con masa y área.
- Volvo FL Electric: la hoja australiana 2026 dice hasta 18,6 t brutos y "280–565 kWh utilizables" (https://www.volvotrucks.com.au/content/dam/volvo-trucks/markets/australia/trucks/brochure-gallery/Volvo_FL_Electric_Fact_Sheet_2026.pdf), pero una hoja alemana de un distribuidor da 160 a 316 kWh utilizables para 200 a 395 kWh nominales. **Las dos no concuerdan; no uso el FL Electric** y no encontré confirmación de venta en Chile.
- Otros en Chile `[SECUNDARIO]`: camión BYD de 13 t de carga útil, 200 km, para CCU (Ministerio de Energía, 21-nov-2018); Foton eAuman 2548 con Sotraser.

---

## 3. Efecto de la velocidad y de la congestión

Esta es la afirmación sobre la que descansa el proyecto. **La evidencia no es unánime** y conviene presentarla así.

### 3.1 Lo que midieron las fuentes

| # | Fuente | Qué midió | Resultado sobre kWh/km vs velocidad |
|---|---|---|---|
| 1 | Öko-Institut 2025 (camiones, Alemania, 19 eActros, >800 eventos) | Consumo medido por evento vs velocidad media del evento | Cae de forma exponencial con la velocidad media: de 20 a 30 km/h, -0,138 kWh/km. Con la ecuación de 1.1: 1,68 kWh/km a 10 km/h vs 0,77 a 30 km/h. Los autores atribuyen el efecto a la energía de arranque y la regeneración limitada, y avisan que se mezcla con el largo del viaje. R² 0,42 para la curva. `[LEÍDO]` |
| 2 | ICCT São Paulo 2023 (2 buses BYD, Línea 6030-10) | Consumo diario vs velocidad media diaria | Correlación negativa **débil**: r = -0,33 (bus 1) y -0,45 (bus 2); velocidades diarias entre 12 y 16 km/h; los autores dicen que no prueba causalidad. `[LEÍDO]` |
| 3 | ICCT São Paulo, prueba SORT (BYD) | Consumo en ciclos estándar, HVAC apagado | Urbano 0,935 kWh/km, mixto y suburbano 0,859: el ciclo urbano gasta 8,9 % más que el mixto (aritmética propia). Las velocidades de cada ciclo no figuran en el informe que leí. `[LEÍDO]` |
| 4 | CARB, Apéndice G (ene-2018): ensayos de pista y dinamómetro de buses 40 ft (Altoona), camiones de drayage clase 8 (UC Riverside) y furgones clase 5 | kWh/milla por ciclo | **"No hay una tendencia obvia entre el consumo del vehículo eléctrico y la velocidad media."** Ejemplos (kWh/milla, convertidos a kWh/km multiplicando por 0,6214 `[INFERENCIA]`): bus CBD (12,7 mph) 1,8 → 1,12 kWh/km; bus Arterial (27 mph) 2,3 → 1,43; bus Commuter (38 mph) 1,5 → 0,93; drayage clase 8: 2,1 kWh/milla (1,30 kWh/km) a 6,6 mph, 2,4 (1,49) a 19,1 mph, 2,0 (1,24) a 50,2 mph. Lo que sí sube al bajar la velocidad es la **ventaja relativa sobre el diésel**: el diésel empeora más rápido que el eléctrico. `[LEÍDO]` |
| 5 | Abdelaty y Mohamed 2021 (bus simulado) | Regresión sobre ciclos construidos | Con detenciones/km, pendiente y HVAC en el modelo, la velocidad media tiene el **menor** peso y signo positivo (+0,07 kWh/km por +10 km/h). La densidad de paradas pesa +0,128 kWh/km por parada/km. `[LEÍDO]` |
| 6 | Resumen de buscador sobre un estudio mesoscópico de buses (Transp. Res. Interdiscip. Perspect. 2025, "Expressing the energy consumption of electric buses with mesoscopic traffic variables") | Consumo neto vs velocidad media | Mínimo cerca de 35 km/h y alza fuerte a velocidades muy bajas; la energía recuperada crece con las detenciones por km. `[NO LEÍDO]`: bloqueado, solo resumen. Es la fuente que más habría que abrir antes del viernes. |
| 7 | Basso 2024 (Santiago) | Importancia de variables con Boruta | La velocidad media, la pendiente media, la masa y el uso de pedales están entre las variables seleccionadas; no entrega curva kWh/km vs velocidad. `[LEÍDO]` |

### 3.2 Lectura `[INFERENCIA]`

1. La cifra de Öko sustenta la afirmación para **camiones reales a velocidad media baja**, pero con la advertencia de los propios autores: parte del efecto es "viaje corto con arranques". Para el pitch esto es compatible con el proyecto, porque una congestión de Santiago significa justamente más arranques y detenciones.
2. Los ensayos de CARB no muestran el efecto en energía absoluta. Las diferencias entre ciclos estándar están dominadas por la masa, el perfil de aceleración y la regeneración de cada ciclo, no por la velocidad media sola. Un jurado informado puede citar este resultado. La defensa honesta es: **el consumo no depende de la velocidad media sola sino de lo que la velocidad baja trae consigo (detenciones, tiempo de auxiliares, regeneración incompleta)**. Por eso el modelo del plan usa detenciones por km y tiempo en vez de solo velocidad.
3. El mecanismo más robusto y verificable por aritmética es el de los **auxiliares**: P_aux/v. A 5 kW, pasar de 20 a 10 km/h suma 0,25 kWh/km. Con los 4–5 kW de Gao 2019 o el 0,2 kWh/km de HVAC de ICCT Santiago, es un efecto de varios por ciento hasta decenas por ciento del consumo de un bus.
4. **No dar un porcentaje de "sobreconsumo por congestión" como cifra publicada.** Ninguna fuente que leí lo entrega para Santiago. El porcentaje del plan ("la misma ruta gasta X % más a las 08:00 que a las 10:30") debe presentarse como **resultado del modelo del equipo**, con su rango de sensibilidad (η_reg, P_aux, Cd·A).
5. Contexto cuantitativo de congestión medido en Santiago: el bus articulado de 18 m opera a ~12 km/h de media y gasta 1,34 kWh/km; la prueba del fabricante en carretera urbana indicaba 1,4 kWh/km (ICCT/CMS 2024, `[LEÍDO]`). Los BYD de 12 m a mayor velocidad en 2017–18 gastaron 1,006 kWh/km (`[SECUNDARIO]`); no son comparables directamente (otro modelo, otro año, otra ruta, otra carga).

---

## 4. Chile y Santiago

### 4.1 Factor de emisión de la red (SEN)

- **Valor para el prototipo: 0,2467 tCO2e/MWh (año 2025)**, igual a 246,72 kgCO2e/MWh. Serie: 2023 = 255,87; 2024 = 213,14; 2025 = 246,72 kgCO2e/MWh (todos con potenciales de calentamiento AR5). `[LEÍDO]`
- **Fuente:** HuellaChile (Ministerio del Medio Ambiente), *Base de datos factores de emisión 2025 para organizaciones y eventos (versión resumen)*, v2, mayo-2026, hoja RESUMEN, fila "Sistema Eléctrico Nacional 2025"; la base cita como origen "Ministerio de Energía 2025 (AR5), Energía Abierta". https://huellachile.mma.gob.cl/wp-content/uploads/2026/05/HuellaChile-Factores-de-emision2025_-Org-y-Ev_v2-Version-resumen.xlsx
- **Cómo se obtuvo:** emisiones netas de GEI del sector generación informadas por el Ministerio de Energía / CNE, divididas por la generación del SEN del año (según la descripción de Energía Abierta; la metodología detallada no la leí).
- **Cuidado:** el valor oscila de un año a otro (2024 fue más bajo que 2023 y 2025). Hay además versiones previas del mismo año: la guía de HuellaChile de nov-2024 daba 0,2421 kgCO2e/kWh para 2023, que ahora figura como 0,2559. Usar el 2025 y citar la fecha de la base. Para análisis horario, el factor medio anual no refleja la hora de carga; decirlo.
- No encontré un factor marginal ni horario oficial.

### 4.2 Diésel

- **Factor de emisión diésel, combustión móvil: 2,740 kgCO2e por litro** (2.740,16 kgCO2e/m³). Misma base HuellaChile 2025, fila "Combustión móvil, Vehículos de pasajeros o carga, Petróleo 2 (Diésel)"; fuente IPCC 2006 vol. 2 cap. 3 con poderes caloríficos del Balance Nacional de Energía (CNE BNE 2024). `[LEÍDO]` Densidad recomendada por HuellaChile: 0,84 t/m³.
- **Consumo típico de un camión diésel urbano:** 21,4 L/100 km para un rígido 4x2 de 12 t en el ciclo regulatorio *Urban Delivery* europeo, línea base de un vehículo típico de 2015 (33,1 L/100 km para un tracto de 40 t en *Long Haul*). Fuente: ICCT, *Fuel efficiency technology in European heavy-duty vehicles: Baseline and potential for the 2020–2030 timeframe* (2017). https://theicct.org/publications/fuel-efficiency-technology-european-heavy-duty-vehicles-baseline-and-potential-2020 . Es **simulación**, vehículo europeo, ciclo de reparto; **no es una medición chilena**. `[LEÍDO]` No encontré una cifra de la Agencia de Sostenibilidad Energética ni del Ministerio de Energía para camión rígido urbano.
- **Cálculo del evitado** `[INFERENCIA]`: 21,4 L/100 km × 2,740 kgCO2e/L = 0,586 kgCO2e/km (solo combustión, sin ciclo de vida del combustible). Un eléctrico de 0,96 kWh/km × 0,2467 kgCO2e/kWh = 0,237 kgCO2e/km (sin pérdidas de carga ni transmisión). La razón es ~2,5 veces menos; con 0,58 kWh/km (eCanter derivado) bajaría a 0,14 kgCO2e/km. Fijar el camión de comparación, porque un 12 t de 2015 en ciclo europeo no es un camión chileno promedio.
- Referencia complementaria `[LEÍDO]`: HuellaChile da 0,1782 kgCO2e por t-km para un camión diésel rígido promedio (DEFRA 2023, guía nov-2024).

### 4.3 Flota de buses eléctricos de Santiago

- **4.088 buses eléctricos en operación (62 % de la flota de Red Movilidad), al 17-feb-2026**, informado por el DTPM. Meta comprometida: 4.400 a marzo de 2026 (68 %). Fuente: Reporte Minero, 17-feb-2026, https://www.reporteminero.cl/noticia/energias-limpias/2026/02/santiago-supera-los-4000-buses-electricos-y-consolida-la-mayor-flota-cero-emision-de-latinoamerica `[LEÍDO]` (nota de prensa que cita al DTPM; no encontré la tabla oficial del DTPM).
- Cierre 2025: 3.849 buses eléctricos (58 %); se incorporaron 1.344 en 2025 (informe de electromovilidad, vía buscador `[SECUNDARIO]`). Regiones: 271 buses eléctricos fuera de Santiago (misma nota).
- Una nota de El Desconcierto (29-sep-2026) dice "según el Ministerio de Transporte, 4.400 unidades" y proyecta más de 4.500 en 2027; no aclara si es conteo o meta, y su encabezado habla de 68 % mientras la foto dice 62 %. **No usar 4.400 como dato medido.**
- Dato de contexto del plan: ANAC informa 1.994 buses electrificados vendidos en 2025 (+555,9 %), 1.769 por renovación de RED y buses regionales. `[LEÍDO]`

### 4.4 Velocidad comercial de RED

- Promedio anual 2025: **16 km/h** (23 km/h en enero 2012). Por franja en 2025: punta mañana 15 a 16 km/h; punta tarde 16 a 17 km/h; resto del día 17 a 18 km/h. En 2012: 23 km/h (punta mañana y día), 21 km/h (punta tarde). Fuente: The Clinic, 19-mar-2026, que cita "un informe compartido por el Ministro de Transportes" (Louis de Grange); https://www.theclinic.cl/2026/03/19/micros-en-santiago-son-cada-vez-mas-lentas-velocidad-promedio-bajo-a-16-km-h-y-ministro-de-grange-apunta-a-aumento-de-autos-y-menos-capacidad-vial-por-ciclovias/ `[SECUNDARIO]`.
- **No encontré el informe de origen (DTPM o MTT)**; el texto de la nota se contradice en una frase ("puntos más altos" vs. "más bajos en 13 años"), y los datos numéricos indican que es "más bajos". Antes de mostrar la cifra en la lámina, abrir el informe en dtpm.cl.
- Las velocidades que mide el propio atlas (GPS) se pueden contrastar con esto: el plan ya lo prevé (Fase 2, "contraste con velocidades comerciales publicadas por DTPM"). Nota: la velocidad comercial incluye paradas y detenciones, por lo que no es comparable con la mediana del atlas que descuenta paraderos.
- Dato propio de ICCT/CMS 2024 `[LEÍDO]`: en rutas 430 y 435 la velocidad media del GPS fue de 12 km/h, con máximas de 50 km/h.

### 4.5 Estrategia Nacional de Electromovilidad (Ministerio de Energía, 2021)

Lanzada el 15-oct-2021. Metas de 100 % de **ventas** cero emisión `[LEÍDO]` en el comunicado oficial del Ministerio de Energía (https://energia.gob.cl/noticias/nacional/lanzamiento-estrategia-nacional-de-electromovilidad-gobierno-anuncia-que-al-2035-se-venderan-solo-vehiculos-electricos-en-chile):

| Año | Categoría |
|---|---|
| 2035 | Vehículos livianos y medianos; transporte público (buses, taxis, colectivos); maquinaria móvil mayor (incluye camiones de extracción mineros) |
| 2040 | Maquinaria móvil menor (construcción, agrícola, forestal) |
| 2045 | **Transporte de carga** y buses interurbanos |

Las metas de **parque** (no de ventas) de la estrategia no figuran en el comunicado que leí, así que no las cito. El comunicado no define "transporte de carga" por tonelaje; la cita a mostrar es la de 2045 para carga. Para la lámina: la meta de carga es de 2045, así que el argumento del proyecto no es "cumplir la meta" sino llegar con la planificación de rutas lista antes de que la flota exista.

### 4.6 Camiones eléctricos de carga en Chile

- **ANAC (Registro Civil), diciembre 2025:** 107 camiones electrificados vendidos en 2025 (-11,6 % frente a 2024); el informe los describe como un segmento "en etapa piloto, con pocos actores y volúmenes marginales". "Electrificados" es el término de ANAC: no confirmé que todos sean 100 % eléctricos a batería. Marcas con más unidades en la tabla: Yutong 24, JMC 15 (la tabla completa no se extrajo bien). Fuente: *Informe Cero y Bajas Emisiones, diciembre 2025*, https://www.anac.cl/wp-content/uploads/2026/01/12-ANAC-Informe-Cero-y-Bajas-Emisiones-Diciembre-2025.pdf `[LEÍDO]`.
- Hasta noviembre 2025, según ANAC (nota de prensa): 89 camiones de "nuevas energías" (20 livianos, 40 medianos, 29 pesados) `[SECUNDARIO]`. Se parece a la cifra anterior, pero con otra definición y corte; no mezclar.
- Proyectos puntuales `[SECUNDARIO]`: BYD de 13 t para CCU (2018); Mercedes eActros 600 de Cavalieri (ene-2026, ruta Santiago–Viña del Mar, bebidas); Foton eAuman con Sotraser; Fuso eCanter (2023).
- **No encontré** un censo del parque de camiones eléctricos en circulación ni telemetría pública de camiones eléctricos en Chile.

### 4.7 Qué es CASE

- **Centro de Aceleración Sostenible de Electromovilidad:** consorcio liderado por la Universidad de Chile (Facultad de Ciencias Físicas y Matemáticas; director citado: Williams Calderón, Depto. de Ingeniería Mecánica), adjudicado por Corfo en dic-2020 como el primer centro de electromovilidad del país, con apoyo de los ministerios de Energía y de Transportes. Financiamiento: Corfo cofinancia hasta el 80 % de un programa que puede llegar a US$7 millones (fondos del contrato de litio Corfo–Albemarle). Objetivos declarados: coordinar a los actores, buscar soluciones para transporte público, privado y **de carga**, ampliar la distribución de carga, capacitar y crear proveedores. Miembros nombrados: Agencia de Sostenibilidad Energética, Centro Mario Molina, U. de Santiago, U. Tecnológica Metropolitana, U. Austral, EY, ElectroMov (otras notas hablan de más de 60 instituciones). `[SECUNDARIO]`
- Fuentes: Minería Chilena, 18-dic-2020, https://www.mch.cl/corfo-anuncia-consorcio-que-desarrollara-primer-centro-para-la-electromovilidad-del-pais/ ; Reporte Minero, ago-2020, https://reporteminero.cl/noticia/noticias/2020/08/conoce-el-consorcio-centro-de-aceleracion-sostenible-para-la-electromovilidad-case .
- **Todas son notas de 2020.** No encontré el sitio oficial de CASE ni su estado actual; confirmar con ellos el día 1 (el plan ya lo prevé) qué proyectos de carga y de telemetría tienen.

---

## 5. Carga

### 5.1 Potencias típicas (hojas de fabricante) `[LEÍDO]`

| Vehículo | Carga lenta (AC) | Carga rápida (DC) | Tiempos del fabricante |
|---|---|---|---|
| Fuso eCanter | 22 kW | 104 kW (CCS2) | 20–80 %: 26 min (M, 82 kWh), 39 min (L, 124 kWh). 0–100 % en AC: 4 h 54 min (M), 6 h 00 (L) |
| Mercedes eEconic (336 kWh) | — | hasta 160 kW | 20–80 %: ~75 min |
| Mercedes eActros 400 / 600 | — | hasta 400 kW (CCS2) | no vistos |
| Volvo FL Electric | 22 kW (16,8 h con 4 paquetes) | 150 kW (2,3 h con 4 paquetes) | solo hoja australiana |
| BYD K9 (12 m) | — | — | ~6 h completa; 3 h en carga rápida (nota de BYD, `[SECUNDARIO]`) |
| Yutong articulado 18 m | — | pistola tipo 2 hasta 300 kW | 2 h en la noche, 40 min al mediodía (ICCT/CMS 2024) |
| Cargador de depósito de bus de referencia | — | 100 kW (New Flyer XE40, Abdelaty 2021) | |

Observación `[INFERENCIA]`: para camiones de reparto de 8 a 19 t las potencias de plataforma son de 100 a 160 kW en DC; en depósito, la carga nocturna AC de 22 kW alcanza (6 h a 22 kW ≈ 130 kWh). Para el optimizador usar 22 kW (depósito) y 100 a 150 kW (oportunidad).

### 5.2 Cargadores en Chile y la RM (SEC)

- **Reporte Nacional de Infraestructura de Recarga para Vehículos Eléctricos 2026** (SEC, datos de 2025; nota de Reporte Minero del 9-sep-2026, https://www.reporteminero.cl/noticia/noticias/2026/09/sec-reporte-nacional-infraestructura-recarga-vehiculos-electricos-chile-2026) `[SECUNDARIO]`:
  - 5.245 cargadores operativos en el país en 2025 (24 en 2018); 4.015 privados (potencia media 11 kW) y 1.230 públicos (23,5 %), que concentran ~50 % de la potencia instalada.
  - **Región Metropolitana: 3.827 cargadores, públicos y privados juntos.** No encontré la cifra solo pública de la RM.
  - Puntos de acceso público: 1.112 lentos (AC), 457 rápidos (DC) y 167 ultrarrápidos (DC): 1.736 en total, que no calza con los 1.230 equipos públicos (la nota no aclara si son conectores vs equipos).
  - Operadores públicos: Copec Voltex 172 instalaciones, Enel Mobility 171, Enex 42 (83 % entre los tres).
  - ANAC (dic-2025): 334 cargadores privados destinados a buses y camiones. `[LEÍDO]`
- El plan ya tiene un conjunto de "cargadores" en los datos externos de la Fase 0; el reporte SEC no da potencias por clase, así que las potencias de 5.1 vienen de los fabricantes.
- Electroterminales de buses: Copec se adjudicó seis para ~559 buses (nota de prensa feb-2026, `[SECUNDARIO]`). No hay cifra pública de electroterminales de uso compartido para camiones.

---

## 6. Tabla resumen (valores recomendados)

Detalle machine-readable en `parametros_energia.json`. Confianza: alta = leída en fuente primaria y con ese uso; media = leída pero con límites o secundaria; baja = supuesto, conjetura o ajeno a Chile.

| Parámetro | Valor | Unidad | Fuente corta | Confianza |
|---|---|---|---|---|
| Factor de emisión SEN 2025 | 0,2467 | tCO2e/MWh | HuellaChile 2025 v2 / Min. Energía | alta |
| Factor de emisión SEN 2024 | 0,2131 | tCO2e/MWh | HuellaChile 2025 v2 | alta |
| Factor de emisión diésel móvil | 2,740 | kgCO2e/L | HuellaChile 2025 / IPCC 2006 | alta |
| Consumo diésel rígido 12 t, reparto | 21,4 | L/100 km | ICCT 2017 (simulación UE) | media |
| Buses eléctricos RED, 17-feb-2026 | 4.088 (62 % de la flota) | buses | DTPM vía Reporte Minero | media |
| Velocidad comercial bus RED, 2025 | 16 (punta mañana 15–16; punta tarde 16–17; día 17–18) | km/h | The Clinic (informe MTT) | media |
| Meta ENE carga: 100 % ventas cero emisión | 2045 | año | Min. Energía 2021 | alta |
| Camiones electrificados vendidos 2025 | 107 | unidades | ANAC dic-2025 | media |
| Cargadores operativos Chile 2025 | 5.245 (1.230 públicos) | cargadores | SEC 2026 vía Reporte Minero | media |
| Cargadores RM (públicos+privados) | 3.827 | cargadores | SEC 2026 vía Reporte Minero | media |
| Carga DC camión liviano (eCanter) | 104 | kW | Fuso 2024 | alta |
| Carga AC de depósito | 22 | kW | Fuso 2024; Volvo | alta |
| Carga DC camión pesado (eEconic) | 160 | kW | Mercedes-Benz Trucks | alta |
| Camión liviano: peso bruto / vacío (eCanter L) | 8.550 / 4.055 | kg | Fuso 2024 | alta |
| Camión liviano: batería utilizable (eCanter L) | 116 | kWh | Fuso 2024 | alta |
| Camión liviano: consumo derivado | 0,58 | kWh/km | cálculo propio sobre Fuso 2024 | baja |
| Camión pesado: batería utilizable (eActros 300) | 291 | kWh | Öko 2025 / Mercedes | media |
| Camión pesado: consumo real medio (11–18 t, 20 °C) | 0,96 | kWh/km | Öko 2025 | media |
| Camión pesado: curva consumo vs velocidad | 5,1304·exp(-0,17·s) + 0,0183·w − 0,0132·t + 0,0015·a + 0,7091 | kWh/km | Öko 2025 | media |
| Camión con refrigeración (TCU) | +0,092 | kWh/km | Öko 2025 | media |
| Bus 12 m: consumo medido en Santiago | 1,006 | kWh/km | Galarza 2020 vía ICCT 2023 | media |
| Bus 18 m: consumo medido en Santiago | 1,34 | kWh/km | ICCT/CMS 2024 | media |
| Bus 12 m: consumo medido en São Paulo | 1,19–1,27 | kWh/km | ICCT 2023 | media |
| Cd bus | 0,7 | — | Basso 2024; Gao 2019 | media |
| Cd camión de reparto | 0,62 | — | Gao 2019 | media |
| Crr bus | 0,01 | — | Basso 2024 | media |
| Crr camión | 0,009 | — | Gao 2019 | baja |
| Área frontal bus | 7,5–7,67 | m² | Basso 2024 (DTPM) | alta |
| Área frontal camión pesado genérico | 9,0 | m² | Gao 2019 | baja |
| Área frontal camión 7,5–12 t | sin fuente (calibrar Cd·A) | m² | — | baja |
| Eficiencia de tracción (motor × transmisión) | 0,82 | — | Basso 2024 (Asamer) | media |
| Fracción regenerada | 0,5–0,65 | — | Basso 2024 (Zhang y Yao) | media |
| Auxiliares bus / camión | 5,0 / 4,0 | kW | Gao 2019 | media |
| HVAC bus Santiago | 0,2 | kWh/km | ICCT/CMS 2024 (supuesto) | baja |
| Efecto +1 parada/km (bus) | +0,128 | kWh/km | Abdelaty 2021 | baja |
| Efecto +1 % pendiente (bus) | +0,380 | kWh/km | Abdelaty 2021 | baja |
| Densidad del aire | 1,16 | kg/m³ | Basso 2024 | media |

---

## 7. Vacíos

No encontré o no pude verificar:

1. **Coeficientes de un modelo mesoscópico para camiones con velocidad, detenciones/km y pendiente.** No existe en lo que encontré. Es el vacío metodológico principal; la mitigación es el modelo físico calibrado de 1.4.
2. **Área frontal, Cd, Crr y eficiencias de las tres fichas de camión** (eCanter, eEconic/eActros, Foton): los fabricantes no los publican en las hojas que leí. Los 0,62/0,009/9,0 m² de Gao 2019 son de un camión genérico de 15,4 t en un simulador de ORNL. Para el de 7,5–12 t no hay fuente; propongo calibrar Cd·A contra el rango certificado.
3. **Consumo en kWh/km certificado o medido del eCanter** y de otros camiones de 7,5–12 t. Solo hay un cociente propio (batería utilizable / rango del fabricante).
4. **Ficha técnica chilena** del eCanter (la leída es la europea) y confirmación de que el eActros 300 se vende en Chile.
5. **Masa en vacío, batería utilizable y área frontal del bus de 12 m** de Santiago: BYD K9FE (324 kWh nominal), Yutong y Foton. La "masa" de la tabla DTPM (12.420 kg) no está definida (vacío o con pasajeros). La batería de 422 kWh de Yutong es conjetura de electrive.
6. **Papers que no pude abrir** (acceso bloqueado): Hjelkrem 2021, Gallet 2018, Fiori 2021, el artículo mesoscópico de 2025 y el de caracterización por velocidad (Energy 2022). Antes del viernes conviene abrir al menos el mesoscópico de 2025 y Hjelkrem desde una red universitaria.
7. **Informe de origen de la velocidad comercial de RED** (DTPM o MTT): solo hay la nota de prensa. Falta también la velocidad "hora punta" en los informes de gestión del DTPM.
8. **Conteo oficial de buses eléctricos posterior a feb-2026**: 4.088 es del DTPM por prensa; 4.400 es meta o cifra del Ministerio sin aclarar.
9. **Cargadores públicos solo de la RM** y potencias por clase: la SEC entrega 3.827 combinados; la diferencia entre conectores (1.736) y equipos públicos (1.230) no está explicada en la nota. No leí el PDF original.
10. **Factor de emisión horario o marginal** y el detalle metodológico del factor SEN: solo el promedio anual.
11. **Consumo de camión diésel medido en Chile**: solo el simulado europeo.
12. **Telemetría de camiones eléctricos en Chile**: nada público. CASE es el candidato a tenerla.
13. **Estado actual de CASE**: solo notas de 2020.
14. **Metas de parque de la ENE** (no solo de ventas): no verificadas.
15. **Efecto directo de la congestión en kWh/km de camiones en ciudad**: no hay medición publicada que aísle congestión de ruta, carga y temperatura; la evidencia (sección 3) es parcial y en parte contradictoria.

### Valores de baja confianza que no debería mostrar sin cuidado

Consumo derivado del eCanter (0,58 kWh/km); Crr y área frontal de camión (Gao, genérico); HVAC 0,2 kWh/km (supuesto de ICCT); los efectos por parada y por pendiente de Abdelaty (simulación de bus de 40 ft); la velocidad comercial de RED (nota de prensa); la curva de Öko por debajo de 20 km/h; el 1,006 kWh/km de Santiago (cita de una cita).

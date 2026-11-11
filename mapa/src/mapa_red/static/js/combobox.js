// Desplegable con buscador (menú M3): reemplaza la lista nativa de un <select>, que no se puede diseñar.
// El <select> queda escondido y sigue guardando el valor, así el resto del código (innerHTML, .value,
// eventos "change") no cambia. Cada opción muestra su nombre y, en gris, un dato secundario (data-sec).
// Un <select> puede ofrecer una salida cuando la búsqueda no encuentra nada: `sel.pistaVacio()` →
// { texto, boton, accion }.
import { TITULO_CODIGO } from "./dominio.js";
import { esc, fmt } from "./util.js";

const MAX_OPCIONES_MENU = 300;  // con listas largas (≈7 000 buses) se muestran las primeras; el buscador encuentra el resto
const MIN_OPCIONES_BUSCADOR = 9;
const ALTO_MAX_MENU = 360, ALTO_MIN_ABAJO = 240, ANCHO_MIN_MENU = 300;
const VALOR_SELECT = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
let menuAbierto = null;

const partesOpcion = o => ({ p: o.text, s: o.dataset.sec || "", chip: o.dataset.chip });

export function mejorarSelects(selects) {
  selects.forEach(mejorarSelect);
  // Clic fuera, scroll del panel o cambio de tamaño cierran el menú (va fijo en pantalla)
  document.addEventListener("mousedown", e => {
    if (menuAbierto && !menuAbierto.menu.contains(e.target) && !menuAbierto.campo.contains(e.target)) cerrarMenu();
  });
  document.addEventListener("scroll", e => { if (menuAbierto && !menuAbierto.menu.contains(e.target)) cerrarMenu(); }, true);
  window.addEventListener("resize", cerrarMenu);
}

function cerrarMenu() {
  if (!menuAbierto) return;
  menuAbierto.menu.remove();
  menuAbierto.campo.classList.remove("abierto");
  menuAbierto.campo.setAttribute("aria-expanded", "false");
  menuAbierto = null;
}

function mejorarSelect(sel) {
  const caja = document.createElement("div");
  caja.className = "cb";
  sel.parentNode.insertBefore(caja, sel);
  const campo = document.createElement("button");
  campo.type = "button"; campo.className = "cb-campo";
  campo.id = sel.id + "_cb";
  campo.setAttribute("aria-haspopup", "listbox"); campo.setAttribute("aria-expanded", "false");
  campo.innerHTML = `<span class="cb-texto" id="${sel.id}_txt"></span><span class="ms" aria-hidden="true">arrow_drop_down</span>`;
  caja.append(campo, sel);
  // La etiqueta visible del <select> pasa al campo: nombre accesible = etiqueta + valor elegido,
  // y un clic en la etiqueta enfoca el campo
  const etiqueta = document.querySelector(`label[for="${sel.id}"]`);
  if (etiqueta) {
    etiqueta.id ||= sel.id + "_lbl";
    etiqueta.htmlFor = campo.id;
    campo.setAttribute("aria-labelledby", `${etiqueta.id} ${sel.id}_txt`);
  }

  const pintarCampo = () => {
    const o = sel.options[sel.selectedIndex];
    // Cerrado muestra solo el nombre (el detalle está en el menú y en las tarjetas de cifras)
    const { p, s } = o ? partesOpcion(o) : { p: "", s: "" };
    campo.querySelector(".cb-texto").textContent = p;
    campo.title = o ? (s ? `${p} · ${s}` : p) : "";
    campo.disabled = sel.disabled;
  };
  // .value = … desde el código y cambios de opciones o de "disabled" repintan el campo
  Object.defineProperty(sel, "value", {
    configurable: true, get: () => VALOR_SELECT.get.call(sel),
    set: v => { VALOR_SELECT.set.call(sel, v); pintarCampo(); } });
  new MutationObserver(pintarCampo).observe(sel, { childList: true, attributes: true, attributeFilter: ["disabled"] });

  function abrir(qInicial = "") {
    cerrarMenu();
    const opciones = [...sel.options];
    const menu = document.createElement("div");
    menu.className = "cb-menu";
    const idLista = sel.id + "_lista", nombre = etiqueta?.textContent.trim() || "opciones";
    menu.innerHTML = (opciones.length >= MIN_OPCIONES_BUSCADOR ? `<div class="cb-buscar"><span class="ms" aria-hidden="true">search</span>` +
      `<input type="text" placeholder="Buscar…" role="combobox" aria-autocomplete="list" aria-expanded="true" ` +
      `aria-controls="${idLista}" aria-label="Buscar en ${esc(nombre)}" autocomplete="off" spellcheck="false"></div>` : "") +
      `<div class="cb-lista" id="${idLista}" role="listbox" aria-label="${esc(nombre)}" tabindex="-1"></div>`;
    document.body.append(menu);
    // Debajo del campo si cabe; si no, encima
    const r = campo.getBoundingClientRect();
    const abajo = innerHeight - r.bottom - 8, arriba = r.top - 8;
    menu.style.left = r.left + "px";
    menu.style.width = Math.max(r.width, ANCHO_MIN_MENU) + "px";  // ancho para nombre + dato en una línea
    if (abajo >= ALTO_MIN_ABAJO || abajo >= arriba) {
      menu.style.top = r.bottom + 4 + "px"; menu.style.maxHeight = Math.min(ALTO_MAX_MENU, abajo) + "px";
    } else {
      menu.style.bottom = innerHeight - r.top + 4 + "px"; menu.style.maxHeight = Math.min(ALTO_MAX_MENU, arriba) + "px";
    }
    const lista = menu.querySelector(".cb-lista"), buscar = menu.querySelector("input");
    let visibles = [], activa = -1;

    const resaltar = (txt, q) => {
      const i = q ? txt.toLowerCase().indexOf(q) : -1;
      return i < 0 ? esc(txt) : esc(txt.slice(0, i)) + `<mark>${esc(txt.slice(i, i + q.length))}</mark>` + esc(txt.slice(i + q.length));
    };
    // La opción activa (flechas) se anuncia sin mover el foco del buscador
    const marcarActiva = () => {
      const id = visibles.length ? `${idLista}_${activa}` : "";
      for (const el of [buscar, lista]) {
        if (!el) continue;
        if (id) el.setAttribute("aria-activedescendant", id); else el.removeAttribute("aria-activedescendant");
      }
    };
    function pintar() {
      const q = (buscar?.value || "").trim().toLowerCase();
      const todos = q ? opciones.filter(o => o.text.toLowerCase().includes(q) || (o.dataset.sec || "").toLowerCase().includes(q)) : opciones;
      visibles = todos.slice(0, MAX_OPCIONES_MENU);
      // Sin resultados por culpa de otros filtros: el select puede ofrecer una salida
      const pista = !todos.length && q && sel.pistaVacio ? sel.pistaVacio() : null;
      activa = Math.max(0, visibles.findIndex(o => o.value === sel.value));
      lista.innerHTML = visibles.map((o, i) => {
        const { p, s, chip } = partesOpcion(o);
        return `<div class="cb-op${o.value === sel.value ? " sel" : ""}${i === activa ? " activa" : ""}" role="option" ` +
          `id="${idLista}_${i}" aria-selected="${o.value === sel.value}" data-i="${i}">` +
          `<span class="p" title="${esc(o.text)}">${resaltar(p, q)}</span>` +
          (chip ? `<span class="cb-chip" title="${TITULO_CODIGO[chip]}">${chip}</span>` : "") +
          (s ? `<span class="s">${esc(s)}</span>` : "") + `</div>`;
      }).join("") +
        (!todos.length ? `<div class="cb-nota">Sin resultados${pista ? `. ${esc(pista.texto)}` : ""}</div>` +
          (pista ? `<button type="button" class="btn-tonal cb-accion"><span class="ms" aria-hidden="true">search</span>${esc(pista.boton)}</button>` : "") : "") +
        (todos.length > visibles.length ? `<div class="cb-nota">Se muestran ${fmt(visibles.length, 0)} de ${fmt(todos.length, 0)}: escribe para acotar</div>` : "");
      lista.querySelector(".activa")?.scrollIntoView({ block: "nearest" });
      marcarActiva();
      const accion = lista.querySelector(".cb-accion");
      if (accion) accion.onclick = async e => {
        e.stopPropagation(); cerrarMenu();
        await pista.accion();
        abrir(buscar.value);  // vuelve a abrir con la misma búsqueda, ahora sobre todos
      };
    }
    const mover = d => {
      if (!visibles.length) return;
      activa = (activa + d + visibles.length) % visibles.length;
      lista.querySelectorAll(".cb-op").forEach((x, i) => x.classList.toggle("activa", i === activa));
      lista.querySelector(".activa")?.scrollIntoView({ block: "nearest" });
      marcarActiva();
    };
    const elegir = i => {
      const o = visibles[i];
      cerrarMenu(); campo.focus();
      if (!o || o.value === sel.value) return;
      sel.value = o.value;
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    };
    lista.onclick = e => { const op = e.target.closest(".cb-op"); if (op) elegir(+op.dataset.i); };
    lista.onmousemove = e => { const op = e.target.closest(".cb-op"); if (op && +op.dataset.i !== activa) mover(+op.dataset.i - activa); };
    menu.onkeydown = e => {
      if (e.key === "ArrowDown") { mover(1); e.preventDefault(); }
      else if (e.key === "ArrowUp") { mover(-1); e.preventDefault(); }
      else if (e.key === "Enter") { elegir(activa); e.preventDefault(); }
      else if (e.key === "Escape" || e.key === "Tab") {
        cerrarMenu(); campo.focus(); e.preventDefault();
        e.stopPropagation();  // Escape cierra solo el menú, no también el aviso de error (keydown del documento)
      }
    };
    if (buscar) { buscar.oninput = pintar; buscar.value = qInicial; }
    pintar();
    (buscar || lista).focus();
    campo.classList.add("abierto"); campo.setAttribute("aria-expanded", "true");
    menuAbierto = { menu, campo };
  }
  campo.onclick = () => (menuAbierto?.campo === campo ? cerrarMenu() : abrir());
  campo.onkeydown = e => { if (e.key === "ArrowDown" || e.key === "ArrowUp") { abrir(); e.preventDefault(); } };
  pintarCampo();
}

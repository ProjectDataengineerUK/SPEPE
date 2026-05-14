/**
 * SPEPE — map_layers.js v2
 * Gerencia 7 camadas temáticas no mapa Leaflet via /api/mapa/choropleth.
 * Cores extraídas do spepe-prototype.html v3.
 * Depende de: Leaflet.js, _geoLayer e reloadMap() globais do spepe-app.html.
 */

// ── Configuração das camadas ───────────────────────────────────────────────

const LAYER_CONFIG = {
  eleitoral:  { label: "🏆 Eleitoral",       campo: "pct_votos" },
  ibge:       { label: "📊 IBGE/IDH",        campo: "idhm" },
  saude:      { label: "🏥 Saúde",           campo: "mortalidade_infantil" },
  seguranca:  { label: "🛡 Segurança",       campo: "homicidios" },
  pesquisas:  { label: "📋 Pesquisas",       campo: "intencao_voto" },
  sentimento: { label: "📱 Sentimento",      campo: "score_sentimento" },
  predicao:   { label: "🔮 Predição 2026",   campo: "p_mean" },
};

// ── Escalas de cor (do protótipo v3) ──────────────────────────────────────

const _COLOR = {
  eleitoral:  v => v >= 60 ? "#1a9641" : v >= 50 ? "#74c476" : v >= 45 ? "#ffffbf" : v >= 40 ? "#fdae61" : "#d7191c",
  ibge:       v => v >= 0.78 ? "#08519c" : v >= 0.72 ? "#3182bd" : v >= 0.65 ? "#6baed6" : v >= 0.58 ? "#bdd7e7" : "#ffffd4",
  saude:      v => v >= 22 ? "#d7191c" : v >= 16 ? "#fdae61" : v >= 12 ? "#ffffbf" : v >= 8 ? "#6baed6" : "#08519c",
  seguranca:  v => v >= 50 ? "#d7191c" : v >= 35 ? "#fdae61" : v >= 20 ? "#ffffbf" : v >= 10 ? "#6baed6" : "#08519c",
  pesquisas:  v => v >= 60 ? "#1a9641" : v >= 50 ? "#74c476" : v >= 45 ? "#ffffbf" : v >= 40 ? "#fdae61" : "#d7191c",
  sentimento: v => v >= 0.5 ? "#1a9641" : v >= 0.15 ? "#74c476" : v >= -0.15 ? "#aaaaaa" : v >= -0.5 ? "#fdae61" : "#d7191c",
  predicao:   v => v >= 0.60 ? "#1a9641" : v >= 0.55 ? "#74c476" : v >= 0.48 ? "#ffffbf" : v >= 0.42 ? "#fdae61" : "#d7191c",
};

const _LEGEND = {
  eleitoral:  [["#1a9641","> 60%"],["#74c476","50–60%"],["#ffffbf","45–50%"],["#fdae61","40–45%"],["#d7191c","< 40%"]],
  ibge:       [["#08519c","≥ 0,78"],["#3182bd","0,72–0,78"],["#6baed6","0,65–0,72"],["#bdd7e7","0,58–0,65"],["#ffffd4","< 0,58"]],
  saude:      [["#08519c","< 8"],["#6baed6","8–12"],["#ffffbf","12–16"],["#fdae61","16–22"],["#d7191c","≥ 22 /mil"]],
  seguranca:  [["#08519c","< 10"],["#6baed6","10–20"],["#ffffbf","20–35"],["#fdae61","35–50"],["#d7191c","≥ 50 /100k"]],
  pesquisas:  [["#1a9641","> 60%"],["#74c476","50–60%"],["#ffffbf","45–50%"],["#fdae61","40–45%"],["#d7191c","< 40%"]],
  sentimento: [["#1a9641","Muito positivo"],["#74c476","Positivo"],["#aaaaaa","Neutro"],["#fdae61","Negativo"],["#d7191c","Muito negativo"]],
  predicao:   [["#1a9641","≥ 60%"],["#74c476","55–60%"],["#ffffbf","48–55%"],["#fdae61","42–48%"],["#d7191c","< 42%"]],
};

// ── Mapeamento sg_uf → CD_UF (IBGE 2 dígitos) ────────────────────────────

const _UF_TO_CDUF = {
  AC:"12",AL:"27",AP:"16",AM:"13",BA:"29",CE:"23",DF:"53",ES:"32",GO:"52",
  MA:"21",MT:"51",MS:"50",MG:"31",PA:"15",PB:"25",PR:"41",PE:"26",PI:"22",
  RJ:"33",RN:"24",RS:"43",RO:"11",RR:"14",SC:"42",SP:"35",SE:"28",TO:"17",
};

// ── Estado interno ────────────────────────────────────────────────────────

let _activeLayer = "eleitoral";
let _layerData = {};   // { sg_uf: value }

// ── API pública ────────────────────────────────────────────────────────────

/**
 * Ativa uma camada: busca dados, redesenha mapa, atualiza legenda.
 */
async function swapLayer(layerName) {
  if (!(layerName in LAYER_CONFIG)) {
    if (layerName === "socio") layerName = "ibge";
    else return;
  }
  _activeLayer = layerName;

  // Eleitoral usa o sistema legado (reloadMap com GeoJSON colorido por pct)
  if (layerName === "eleitoral") {
    _layerData = {};
    _applyStyle();
    _updateLegend(layerName);
    if (typeof reloadMap === "function") reloadMap();
    return;
  }

  const data = await _fetchChoropleth(layerName);
  _layerData = {};
  (data || []).forEach(row => {
    if (row.sg_uf) _layerData[row.sg_uf] = row.value;
  });

  _applyStyle();
  _updateLegend(layerName);
}

/**
 * Aplica uf_values recebidos diretamente (ex: do Supervisor via WS).
 */
function applyUfValues(layerName, ufValues) {
  if (!(layerName in LAYER_CONFIG) && layerName !== "socio") return;
  if (layerName === "socio") layerName = "ibge";
  _activeLayer = layerName;
  _layerData = {};
  Object.entries(ufValues || {}).forEach(([uf, val]) => {
    _layerData[uf.toUpperCase()] = parseFloat(val) || 0;
  });
  _applyStyle();
  _updateLegend(layerName);
}

/**
 * Retorna cor para (layer, value) — exposto para uso externo.
 */
function getLayerColor(layerName, value) {
  const fn = _COLOR[layerName] || _COLOR.ibge;
  return (value != null && !isNaN(value)) ? fn(value) : "#21262d";
}

/**
 * Inicializa: expande layer bar para 7 botões e registra cliques.
 */
function initMapLayers() {
  _expandLayerBar();
  document.querySelectorAll(".layer-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".layer-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      swapLayer(btn.dataset.layer);
    });
  });
}

// ── Helpers privados ───────────────────────────────────────────────────────

async function _fetchChoropleth(layer) {
  try {
    const cargo = document.getElementById("map-cargo-sel")?.value || "Governador";
    const cand = document.getElementById("map-candidato")?.value?.trim() || "";
    const ano = document.getElementById("f-ano")?.value || "2022";
    const params = new URLSearchParams({ layer, cargo, ano });
    if (cand) params.set("candidato", cand);
    const url = `/api/mapa/choropleth?${params}`;
    const res = await (typeof apiFetch === "function" ? apiFetch(url) : fetch(url));
    if (!res || !res.ok) return [];
    const json = await res.json();
    return json.data || [];
  } catch {
    return [];
  }
}

function _applyStyle() {
  if (typeof _geoLayer === "undefined" || !_geoLayer) return;
  if (_activeLayer === "eleitoral") return;  // legado usa renderGeoJSON

  const colorFn = _COLOR[_activeLayer] || _COLOR.ibge;
  _geoLayer.setStyle(feature => {
    const props = feature.properties || {};
    // Tenta sg_uf direto, depois converte CD_UF → sg_uf
    const sgUf = props.SIGLA_UF || props.sg_uf || _cdufToSgUf(
      String(props.CD_UF || props.codarea || props.CD_MUN?.slice(0, 2) || "")
    );
    const val = _layerData[sgUf];
    const fill = (val != null && !isNaN(val)) ? colorFn(val) : "#21262d";
    return { fillColor: fill, fillOpacity: 0.72, color: "#4b6878", weight: 1 };
  });
}

function _cdufToSgUf(cduf) {
  const inv = Object.fromEntries(Object.entries(_UF_TO_CDUF).map(([k, v]) => [v, k]));
  return inv[cduf] || cduf;
}

function _updateLegend(layer) {
  const el = document.getElementById("map-legend-items");
  if (!el) return;
  const items = _LEGEND[layer] || [];
  el.innerHTML = items.map(
    ([color, label]) =>
      `<div class="leg-item"><div class="leg-dot" style="background:${color}"></div><span class="leg-label">${label}</span></div>`
  ).join("");
  const title = document.getElementById("map-legend-title");
  if (title) title.textContent = (LAYER_CONFIG[layer] || {}).label || layer;
}

function _expandLayerBar() {
  const bar = document.getElementById("layer-controls");
  if (!bar) return;

  // Só expande se ainda tiver apenas os 4 botões originais
  const existing = [...bar.querySelectorAll(".layer-btn")].map(b => b.dataset.layer);
  const toAdd = [
    { layer: "saude",     label: "🏥 Saúde" },
    { layer: "seguranca", label: "🛡 Segurança" },
    { layer: "pesquisas", label: "📋 Pesquisas" },
  ];
  toAdd.forEach(({ layer, label }) => {
    if (!existing.includes(layer)) {
      const btn = document.createElement("button");
      btn.className = "layer-btn";
      btn.dataset.layer = layer;
      btn.textContent = label;
      bar.appendChild(btn);
    }
  });
}

// ── Exports globais ────────────────────────────────────────────────────────

window.swapLayer      = swapLayer;
window.applyUfValues  = applyUfValues;
window.getLayerColor  = getLayerColor;
window.LAYER_CONFIG   = LAYER_CONFIG;

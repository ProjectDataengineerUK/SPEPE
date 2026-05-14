/**
 * SPEPE — map_layers.js
 * Gerencia os 4 LayerGroups temáticos do mapa Leaflet.
 * Depende de: Leaflet.js (carregado antes), gMap/dataLayer globais do spepe-app.html.
 */

const LAYER_CONFIG = {
  eleitoral:  { label: "Resultado Eleitoral", defaultMetric: "pct_votos",      endpoint: null },
  ibge:       { label: "Indicadores IBGE",    defaultMetric: "idhm",           endpoint: "/api/indicadores/ibge" },
  sentimento: { label: "Sentimento",          defaultMetric: "sentiment_score", endpoint: "/api/indicadores/sentimento" },
  previsao:   { label: "Previsão 2026",       defaultMetric: "pct_previsto",    endpoint: "/api/indicadores/previsao" },
};

// Estado interno do módulo
let _activeLayer = "eleitoral";
let _layerData   = {};   // { cd_municipio_ibge: valor }
let _activeMetric = "pct_votos";

/**
 * Registra listeners nos botões de toggle de camada.
 * Chamado após o mapa estar pronto (window.onload ou initMap).
 */
function initMapLayers() {
  document.querySelectorAll(".layer-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".layer-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      swapLayer(btn.dataset.layer);
    });
  });
}

/**
 * Troca a camada ativa. Busca dados do endpoint se necessário e redesenha.
 * @param {string} layerName  — chave de LAYER_CONFIG
 */
async function swapLayer(layerName) {
  if (!(layerName in LAYER_CONFIG)) return;
  _activeLayer = layerName;
  const cfg = LAYER_CONFIG[layerName];
  _activeMetric = cfg.defaultMetric;

  if (layerName === "eleitoral") {
    _layerData = {};
    _redrawCurrentLayer();
    _updateLayerLegend(layerName);
    return;
  }

  const params = _buildParams(layerName);
  const data = await _fetchLayerData(cfg.endpoint, params);
  if (!data) return;

  _layerData = {};
  (data.data || []).forEach(row => {
    const key = String(row.cd_municipio_ibge || "");
    if (key) _layerData[key] = _extractValue(row, cfg.defaultMetric);
  });

  _redrawCurrentLayer();
  _updateLayerLegend(layerName);
}

/**
 * Recarrega os dados da camada atual com novos params (candidato, uf, etc.).
 * @param {string} layerName
 * @param {Object} params
 */
async function updateLayerData(layerName, params) {
  const cfg = LAYER_CONFIG[layerName];
  if (!cfg || !cfg.endpoint) return;
  const data = await _fetchLayerData(cfg.endpoint, params);
  if (!data) return;

  _layerData = {};
  (data.data || []).forEach(row => {
    const key = String(row.cd_municipio_ibge || "");
    if (key) _layerData[key] = _extractValue(row, cfg.defaultMetric);
  });
  _redrawCurrentLayer();
}

/**
 * Retorna cor hex para uma métrica e valor dados.
 * @param {string} metric
 * @param {number} value
 * @returns {string}  hex color
 */
function getColorScale(metric, value) {
  if (value === null || value === undefined || isNaN(value)) return "#21262d";

  if (metric === "pct_votos" || metric === "pct_previsto") {
    if (value >= 50) return "#1a9641";
    if (value >= 45) return "#a6d96a";
    if (value >= 35) return "#ffffbf";
    if (value >= 30) return "#fdae61";
    return "#d7191c";
  }

  if (metric === "idhm" || metric === "renda_per_capita") {
    // idhm 0–1, renda normalizada
    const norm = metric === "idhm" ? value : Math.min(value / 3000, 1);
    if (norm >= 0.8) return "#08519c";
    if (norm >= 0.65) return "#3182bd";
    if (norm >= 0.5) return "#6baed6";
    if (norm >= 0.35) return "#bdd7e7";
    return "#ffffd4";
  }

  if (metric === "taxa_analfabetismo") {
    // maior analfabetismo → pior (vermelho)
    if (value <= 5) return "#08519c";
    if (value <= 10) return "#6baed6";
    if (value <= 20) return "#ffffbf";
    if (value <= 30) return "#fdae61";
    return "#d7191c";
  }

  if (metric === "sentiment_score") {
    // -1 a +1
    if (value >= 0.6) return "#1a9641";
    if (value >= 0.2) return "#a6d96a";
    if (value >= -0.2) return "#f0f0f0";
    if (value >= -0.6) return "#fdae61";
    return "#d7191c";
  }

  // fallback neutro
  return "#3d444d";
}

/**
 * Callback de estilo para features do Google Maps Data Layer.
 * Usa _layerData e _activeLayer para determinar cor.
 * @param {google.maps.Data.Feature} feature
 * @returns {google.maps.Data.StyleOptions}
 */
function applyStyleToFeature(feature) {
  if (_activeLayer === "eleitoral") return null;

  const props = (feature && feature.properties) || {};
  const cod = String(props.codarea || props.CD_MUN || props.CD_UF || "");
  const valor = _layerData[cod] ?? _layerData[cod.slice(0, 6)];
  const color = (valor !== undefined) ? getColorScale(_activeMetric, valor) : "#21262d";

  return { fillColor: color, fillOpacity: 0.65, color: "#4b6878", weight: 1 };
}

// ── helpers privados ──────────────────────────────────────────────────────

function _buildParams(layerName) {
  const uf = document.getElementById("f-uf")?.value || "SP";
  const candidato = document.getElementById("map-candidato")?.value?.trim() || "";
  const params = new URLSearchParams();
  const cfg = LAYER_CONFIG[layerName];
  params.set("metrica", cfg.defaultMetric);
  if (uf && uf !== "BR") params.set("uf", uf);
  if (candidato) params.set("candidato", candidato);
  if (layerName === "sentimento") {
    params.set("data_ref", new Date().toISOString().slice(0, 10));
  }
  return params;
}

async function _fetchLayerData(endpoint, params) {
  try {
    const url = `${endpoint}?${params.toString()}`;
    const res = await (typeof apiFetch === "function" ? apiFetch(url) : fetch(url));
    if (!res || !res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function _extractValue(row, metric) {
  if (row.valor !== undefined) return row.valor;
  if (row[metric] !== undefined) return row[metric];
  return null;
}

function _redrawCurrentLayer() {
  if (_activeLayer === "eleitoral") {
    if (typeof reloadMap === "function") reloadMap();
    return;
  }
  if (typeof _geoLayer !== "undefined" && _geoLayer && typeof _geoLayer.setStyle === "function") {
    _geoLayer.setStyle(applyStyleToFeature);
  }
}

function _updateLayerLegend(layerName) {
  const el = document.getElementById("map-legend-items");
  if (!el) return;

  const legends = {
    eleitoral: [
      { color: "#1a9641", label: "> 50%" },
      { color: "#a6d96a", label: "45–50%" },
      { color: "#ffffbf", label: "35–45%" },
      { color: "#fdae61", label: "30–35%" },
      { color: "#d7191c", label: "< 30%" },
    ],
    ibge: [
      { color: "#08519c", label: "Muito alto" },
      { color: "#3182bd", label: "Alto" },
      { color: "#6baed6", label: "Médio" },
      { color: "#bdd7e7", label: "Baixo" },
      { color: "#ffffd4", label: "Muito baixo" },
    ],
    sentimento: [
      { color: "#1a9641", label: "Positivo forte" },
      { color: "#a6d96a", label: "Positivo" },
      { color: "#f0f0f0", label: "Neutro" },
      { color: "#fdae61", label: "Negativo" },
      { color: "#d7191c", label: "Negativo forte" },
    ],
    previsao: [
      { color: "#1a9641", label: "> 50% projetado" },
      { color: "#a6d96a", label: "45–50%" },
      { color: "#ffffbf", label: "35–45%" },
      { color: "#fdae61", label: "30–35%" },
      { color: "#d7191c", label: "< 30%" },
    ],
  };

  const items = legends[layerName] || [];
  el.innerHTML = items
    .map(i => `<div class="leg-item"><div class="leg-dot" style="background:${i.color}"></div><span class="leg-label">${i.label}</span></div>`)
    .join("");
}

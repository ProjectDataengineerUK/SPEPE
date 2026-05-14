/* SPEPE — DashboardCommand v1 reducer
   Bridge between Supervisor structured intents and the live dashboard UI.
   Vanilla JS — depends on globals already exported by spepe-app.html:
     reloadMap, applyFilters, switchTab, hlCard, _mapCtx, _mapLevel, gMap */

(function (global) {
  'use strict';

  const UF_CENTROIDS = {
    AC: [-9.0238, -70.8120], AL: [-9.5713, -36.7820], AP: [1.4144, -51.7865],
    AM: [-3.4168, -65.8561], BA: [-12.5797, -41.7007], CE: [-5.4984, -39.3206],
    DF: [-15.7998, -47.8645], ES: [-19.1834, -40.3089], GO: [-15.8270, -49.8362],
    MA: [-4.9609, -45.2744], MT: [-12.6819, -56.9211], MS: [-20.7722, -54.7852],
    MG: [-18.5122, -44.5550], PA: [-3.4168, -52.0000], PB: [-7.2399, -36.7820],
    PR: [-24.8932, -51.4263], PE: [-8.8137, -36.9541], PI: [-7.7183, -42.7289],
    RJ: [-22.9099, -43.2095], RN: [-5.4026, -36.9541], RS: [-30.0346, -53.2179],
    RO: [-11.5057, -63.5806], RR: [2.7376, -62.0751], SC: [-27.2423, -50.2189],
    SP: [-22.0184, -47.0626], SE: [-10.5741, -37.3857], TO: [-10.1753, -48.2982]
  };

  const LAYER_TO_DOM = {
    sentimento: 'social',
    eleitoral: 'electoral',
    ibge: 'economic',
    previsao: 'electoral'
  };

  const METRIC_TO_LAYER = {
    idhm: 'economic', renda: 'economic', gini: 'economic',
    pct_votos: 'electoral', sentiment_score: 'social', prediction_pct: 'electoral'
  };

  const METRIC_TO_COLORBY = {
    pct_votos: 'lider', sentiment_score: 'margem', prediction_pct: 'margem',
    idhm: 'lider', renda: 'lider', gini: 'margem'
  };

  function applyDashboardUpdate(update) {
    if (!update || update.intent_schema !== 'v1') return;
    const actions = Array.isArray(update.actions) ? update.actions : [];
    actions.forEach((a, i) => setTimeout(() => applyAction(a), i * 120));
    if (update.narration) showNarration(update.narration);
  }

  function applyAction(action) {
    if (!action || !action.type) return;
    try {
      switch (action.type) {
        case 'set_layer':   return setLayer(action.layer);
        case 'set_filter':  return setFilter(action.uf, action.cargo, action.candidato);
        case 'zoom_to':     return zoomTo(action.uf, action.municipio);
        case 'highlight':   return highlightMunicipios(action.municipios || []);
        case 'set_metric':  return setMetric(action.metric);
        case 'set_period':  return setPeriod(action.from, action.to);
      }
    } catch (e) {
      console.warn('[dashboard_actions] action failed', action.type, e);
    }
  }

  function setLayer(layer) {
    if (!layer) return;
    _ensureMapTab();
    // Prefer swapLayer from map_layers.js (themed choropleth system)
    if (typeof global.swapLayer === 'function') {
      global.swapLayer(layer);
      document.querySelectorAll('.layer-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.layer === layer);
      });
      return;
    }
    // Fallback: old hidden-select system
    const sel = document.getElementById('map-layer-sel');
    const domLayer = LAYER_TO_DOM[layer] || layer;
    if (sel) {
      sel.value = domLayer;
      if (typeof global.reloadMap === 'function') setTimeout(global.reloadMap, 80);
    }
  }

  function setFilter(uf, cargo, candidato) {
    if (uf) {
      const u = document.getElementById('f-uf');
      if (u) u.value = uf;
      const mu = document.getElementById('map-uf');
      if (mu) mu.value = uf;
    }
    if (cargo) {
      const cargoNorm = _normalizeCargo(cargo);
      const c = document.getElementById('f-cargo');
      if (c) c.value = cargoNorm;
      const mc = document.getElementById('map-cargo-sel');
      if (mc) mc.value = cargoNorm;
    }
    if (candidato) {
      const cinp = document.getElementById('map-candidato');
      if (cinp) cinp.value = candidato;
    }
    if (typeof global.applyFilters === 'function') setTimeout(global.applyFilters, 60);
    if (typeof global.reloadMap === 'function') setTimeout(global.reloadMap, 200);
  }

  function zoomTo(uf, municipio) {
    _ensureMapTab();
    const map = global.gMap;
    if (!map || typeof map.flyTo !== 'function') {
      if (uf) setFilter(uf, null, null);
      return;
    }
    if (municipio) {
      _findMunicipioCentroid(uf, municipio).then(coords => {
        if (coords) map.flyTo(coords, 10, { duration: 1.2 });
        else if (uf && UF_CENTROIDS[uf]) map.flyTo(UF_CENTROIDS[uf], 7, { duration: 1.0 });
      });
      return;
    }
    if (uf && UF_CENTROIDS[uf]) map.flyTo(UF_CENTROIDS[uf], 7, { duration: 1.0 });
  }

  function highlightMunicipios(ids) {
    if (!Array.isArray(ids) || !ids.length) return;
    _ensureMapTab();
    global.__spepeHighlightIds = ids.map(String);
    const map = global.gMap;
    if (!map || !map.eachLayer) return;
    map.eachLayer(layer => {
      if (!layer.feature || !layer.feature.properties) return;
      const props = layer.feature.properties;
      const id = String(props.CD_MUN || props.cd_municipio || props.id || '');
      if (global.__spepeHighlightIds.includes(id) && typeof layer.setStyle === 'function') {
        layer.setStyle({ weight: 3, color: '#facc15', fillOpacity: 0.85 });
        if (layer.bringToFront) layer.bringToFront();
      }
    });
  }

  function setMetric(metric) {
    if (!metric) return;
    const targetLayer = METRIC_TO_LAYER[metric];
    if (targetLayer) {
      const sel = document.getElementById('map-layer-sel');
      if (sel) sel.value = targetLayer;
    }
    const colorBy = METRIC_TO_COLORBY[metric];
    if (colorBy) {
      const cb = document.getElementById('map-color-by');
      if (cb) cb.value = colorBy;
    }
    _ensureMapTab();
    if (typeof global.reloadMap === 'function') setTimeout(global.reloadMap, 100);
  }

  function setPeriod(from, to) {
    if (!from && !to) return;
    global.__spepePeriod = { from, to };
    if (from) {
      const year = String(from).slice(0, 4);
      const ano = document.getElementById('f-ano');
      if (ano && [...ano.options].some(o => o.value === year)) {
        ano.value = year;
        if (typeof global.applyFilters === 'function') setTimeout(global.applyFilters, 60);
      }
    }
  }

  function showNarration(text) {
    if (!text) return;
    let box = document.getElementById('agent-narration');
    if (!box) {
      box = document.createElement('div');
      box.id = 'agent-narration';
      box.style.cssText =
        'margin:8px 16px;padding:10px 14px;background:rgba(34,197,94,.08);' +
        'border-left:3px solid #22c55e;border-radius:6px;color:#cbd5e1;' +
        'font-size:.78rem;line-height:1.45;font-family:Inter,system-ui,sans-serif;' +
        'max-width:100%;white-space:pre-wrap';
      const mapBody = document.querySelector('.map-body') || document.body;
      mapBody.parentNode.insertBefore(box, mapBody.nextSibling);
    }
    const safe = String(text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    box.innerHTML = safe.replace(/\n/g, '<br>');
    box.style.opacity = '0';
    requestAnimationFrame(() => {
      box.style.transition = 'opacity .4s ease';
      box.style.opacity = '1';
    });
  }

  function _ensureMapTab() {
    const mapTab = [...document.querySelectorAll('.main-tab')]
      .find(t => t.textContent.includes('Mapa'));
    if (mapTab && !mapTab.classList.contains('active') && typeof global.switchTab === 'function') {
      global.switchTab(mapTab, 'Mapa');
    }
  }

  function _normalizeCargo(cargo) {
    const c = String(cargo).toLowerCase();
    if (c.includes('presid')) return 'Presidente';
    if (c.includes('govern')) return 'Governador';
    if (c.includes('senad')) return 'Senador';
    if (c.includes('federal')) return 'Dep. Federal';
    if (c.includes('estad')) return 'Dep. Estadual';
    return cargo;
  }

  async function _findMunicipioCentroid(uf, municipio) {
    if (!uf) return null;
    try {
      const resp = await fetch(`/static/geo_mun_${uf}.geojson`);
      if (!resp.ok) return null;
      const gj = await resp.json();
      const target = String(municipio);
      const feat = gj.features.find(f => {
        const p = f.properties || {};
        return String(p.CD_MUN || p.cd_municipio || p.id || '') === target;
      });
      if (!feat) return null;
      return _featureCentroid(feat);
    } catch {
      return null;
    }
  }

  function _featureCentroid(feature) {
    const coords = _flattenCoords(feature.geometry);
    if (!coords.length) return null;
    let lat = 0, lng = 0;
    coords.forEach(c => { lng += c[0]; lat += c[1]; });
    return [lat / coords.length, lng / coords.length];
  }

  function _flattenCoords(geom) {
    if (!geom) return [];
    if (geom.type === 'Point') return [geom.coordinates];
    if (geom.type === 'Polygon') return geom.coordinates.flat();
    if (geom.type === 'MultiPolygon') return geom.coordinates.flat(2);
    return [];
  }

  global.applyDashboardUpdate = applyDashboardUpdate;
  global.applyAction = applyAction;
  global.setLayer = setLayer;
  global.setFilter = setFilter;
  global.zoomTo = zoomTo;
  global.highlightMunicipios = highlightMunicipios;
  global.setMetric = setMetric;
  global.setPeriod = setPeriod;
  global.showNarration = showNarration;
})(window);

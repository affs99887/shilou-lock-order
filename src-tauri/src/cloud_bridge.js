// Runs only in the VSS document. This window has no Tauri IPC permissions.
(() => {
  if (window.top !== window || location.origin !== 'https://v-ss.me' || location.pathname !== '/iphone/stock/') return;
  const nativeFetch = window.fetch.bind(window);
  // Rust owns scheduling; prevent the site's initial query and optional timer
  // from spending a second request budget alongside the app's scheduler.
  window.fetch = (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin === location.origin && url.pathname === '/iphone/api/stock.php') {
      return Promise.reject(new DOMException('由拾漏锁单安排查询', 'AbortError'));
    }
    return nativeFetch(input, init);
  };
  let active;
  window.__apwVssCancel = () => active?.abort();
  window.__apwVssQuery = async request => {
    active?.abort();
    const controller = new AbortController(); active = controller;
    const timer = setTimeout(() => controller.abort(), 20000);
    const read = async path => {
      const response = await nativeFetch(path, {cache:'no-store', signal:controller.signal});
      if (!response.ok) throw new Error(`云端服务 HTTP ${response.status}`);
      return response.json();
    };
    let result;
    try {
      const [stores, products] = await Promise.all([
        read('/iphone/stock/data/stores.json'), read('/iphone/stock/data/products.json')
      ]);
      const store = stores.find(s => s.id === request.store && s.region === request.region);
      if (!store) throw new Error('VSS 尚未提供该地区门店，请选择本机查询');
      const supported = new Set(Object.values(products).flatMap(p => Object.values(p.regionParts?.[request.region] || {})));
      if (!request.parts.length || request.parts.some(p => !supported.has(p))) throw new Error('VSS 尚未提供所选型号，请选择本机查询或更换目标');
      const token = (await read('/iphone/api/token.php')).k;
      if (!token) throw new Error('无法建立 VSS 网页会话，请稍后重试');
      const params = new URLSearchParams({store:store.id, parts:request.parts.join(','), region:store.region, postal:store.postalCode || '', k:token});
      const response = await nativeFetch('/iphone/api/stock.php?' + params, {cache:'no-store', signal:controller.signal});
      const responseText = await response.text();
      let data = null;
      try { data = JSON.parse(responseText); } catch (_) {}
      result = {nonce:request.nonce, http:response.status, data,
        responseText:responseText.slice(0,2048), responseTruncated:responseText.length > 2048};
    } catch (e) {
      result = {nonce:request.nonce, error:e.name === 'AbortError' ? '云端查询超时或已取消' : String(e.message || e)};
    } finally {clearTimeout(timer);}
    if (active !== controller) return;
    location.href = 'https://apw-vss.invalid/result?payload=' + encodeURIComponent(JSON.stringify(result));
  };
})();

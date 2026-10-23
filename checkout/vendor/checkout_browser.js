/* Safari DOM adapter. Called by the local runner with an explicit request.
 * Snapshot output excludes contact values, tokens, full URL and page text.
 * Ordinary commands cannot submit. A separate one-shot submit command requires
 * the runner's explicit formal-mode authorization and fresh review validation.
 */
function appleCheckout(request) {
  "use strict";
  if (window.__appleCheckoutDeparting) return {stage:"LOADING"};
  if (location.href.startsWith("about:blank")) return {stage: "LOADING"};
  const norm = x => String(x || "").replace(/\s+/g, "").toLowerCase();
  const allowed = host => /^(www\.|secure\d*\.www\.|secure\.store\.|store\.)?apple\.com\.cn$/.test(host);
  if (location.protocol !== "https:" || !allowed(location.hostname)) return {stage: "FOREIGN"};
  const nav = performance.getEntriesByType("navigation")[0];
  const http_status = nav && nav.responseStatus || 0;
  // A denial can use Apple's /shop/404 error template. Preserve the actual
  // status before classifying the URL so it cannot trigger entry retries.
  if ([403,429,541].includes(http_status)) return {stage:"BLOCKED",http_status};
  if (/\/shop\/404\/?$/.test(location.pathname)) return {stage:"NOT_FOUND"};
  if (/\/shop\/sorry\/session_expired\/?$/i.test(location.pathname)) return {stage:"SESSION_EXPIRED"};
  const accountRedirect = /^\/shop\/account(?:\/|$)/i.test(request.expected_path || "") &&
    /^\/shop\/account(?:\/|$)/i.test(location.pathname);
  const expectedPathMismatch = request.expected_path && !location.pathname.startsWith(request.expected_path) && !accountRedirect &&
      !/\/shop\/signIn/i.test(location.pathname);
  const visible = e => !!e && !e.closest('[hidden],[aria-hidden="true"]') &&
    !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const disabled = e => !!e.disabled || e.getAttribute("aria-disabled") === "true";
  function visibleText(element){
    if(!element)return '';
    const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT),parts=[];
    while(walker.nextNode())if(visible(walker.currentNode.parentElement))parts.push(walker.currentNode.textContent);
    return parts.join(' ');
  }
  const root = document.querySelector("main,[role=main],#main") || document.body;
  if (!root) return {stage: "LOADING"};
  const text = root.innerText || root.textContent || "";
  // Conservative receipt recognition. Unknown/payment-provider redirects do not
  // count as an order. Never return the actual order identifier or page text.
  const receiptText = visibleText(root);
  const receiptIds = [...receiptText.matchAll(/(?:订单号|订单编号)\s*[:：]?\s*(W\d{8,16})\b/g)].map(m=>m[1]);
  const pendingReceipt = new Set(receiptIds).size===1 &&
    /(?:订单已提交|订单已收到|感谢.*订购|感谢.*订单)/.test(receiptText) &&
    /(?:等待付款|等待支付|待付款|待支付|请完成付款|请完成支付)/.test(receiptText) &&
    !/(?:订单已取消|订单已失效|付款失败|支付失败)/.test(receiptText);
  const ntext = norm(text);
  if (document.readyState === "loading" && ntext.length < 100) return {stage:"LOADING"};
  const path = location.pathname.toLowerCase();
  const finalPattern = /立即下单|现在下单|提交订单|确认订单|立即付款|现在付款|确认付款|placeorder|paynow|立即购买|购买并支付/;
  const forbidden = e => finalPattern.test(norm(label(e))) || /同意.*条款|接受.*协议|签署|授权.*(?:征信|授信)|开通.*(?:花呗|白条)|申请.*(?:贷款|授信)/.test(norm(label(e)));
  function label(e) {
    const labelText = l => (l.innerText || l.textContent || "").trim() ||
      [...l.querySelectorAll('img[alt]')].filter(visible).map(i=>i.alt).join(" ");
    const labels = e.labels ? [...e.labels].map(labelText).join(" ") : "";
    const labelledBy = (e.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean)
      .map(id => document.getElementById(id)?.textContent || "").join(" ");
    return e.getAttribute("aria-label") || labelledBy || labels || e.innerText || e.textContent || e.value || "";
  }
  function selected(e) {
    return !!e.checked || e.getAttribute("aria-checked") === "true" ||
      e.getAttribute("aria-pressed") === "true" || e.getAttribute("aria-selected") === "true";
  }
  const registry = window.__appleCheckoutV2 || (window.__appleCheckoutV2 = {
    refs: new Map(), ids: new WeakMap(), serial: 0, executed: new Set(), document: document
  });
  const ref = e => {
    if (!registry.ids.has(e)) {
      const id = String(++registry.serial);
      registry.ids.set(e, id); registry.refs.set(id, e);
    }
    return registry.ids.get(e);
  };
  function fieldKey(e) {
    const context = norm([e.name, e.id, e.autocomplete, e.placeholder, label(e)].join(" "));
    if (/pickup|retail|store|取货|门店|零售店/.test(context) && /location|search|postal|zip|城市|地区|邮编|搜索/.test(context)) return "pickup_location";
    // Specific semantic fields precede generic "联系人"/"姓名".
    const rules = [
      ["id_last4", /身份证.*后四位|证件.*后四位|governmentidlast4|identitylast4/],
      ["phone", /tel|telephone|mobile|phone|手机|电话/],
      ["email", /email|e-mail|邮箱|电子邮件/],
      ["family_name", /family-name|familyname|lastname|last_name|姓氏/],
      ["given_name", /given-name|givenname|firstname|first_name|名字/],
      ["postal_code", /postal-code|postalcode|zipcode|zip_code|邮编|邮政编码/],
      ["province", /address-level1|province|省份|省\/直辖市/],
      ["city", /address-level2|city|locality|城市/],
      ["district", /address-level3|district|county|区县|区\/县/],
      ["address", /street-address|address-line1|address1|street|详细地址|街道地址|门牌/],
      ["invoice_title", /invoicetitle|invoice_title|发票抬头/],
      ["tax_id", /taxid|tax_id|taxpayer|税号|纳税人识别号/],
      ["full_name", /fullname|full_name|pickupname|recipientname|收货人|取货人|联系人|姓名/]
    ];
    return (rules.find(([,rx]) => rx.test(context)) || [""])[0];
  }
  function scheduleKey(e) {
    const context = norm([e.name,e.id,e.getAttribute("data-autom"),label(e)].join(" "));
    if (/pickuptime|pickup-time|timeslot|time-slot|checkinwindow|check-in-window|checkintime|check-in-time|取货时间|取货时段|签到时段|签到时间/.test(context)) return "pickup_time";
    if (/pickupdate|pickup-date|取货日期/.test(context)) return "pickup_date";
    return "";
  }
  function scheduleOption(o, key) {
    const t = norm(o.textContent);
    return !!o.value && !o.disabled && !o.parentElement.disabled &&
      !/请选择|可选时段|选择时段|select|choose|loading|加载|无可用|暂无|已满/.test(t) &&
      (key !== "pickup_time" || /\d{1,2}[:：]\d{2}/.test(t));
  }
  function safeValue(e, value) {
    if (!/^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName) ||
        (e.tagName === "INPUT" && !["text","tel","email","number","search"].includes(e.type))) return false;
    const old = e.value;
    let v = String(value);
    if (e.tagName === "SELECT") {
      const matches = [...e.options].filter(o => !o.disabled && !o.parentElement.disabled &&
        (norm(o.value) === norm(v) || norm(o.textContent) === norm(v)));
      if (matches.length !== 1) return false;
      const option = matches[0];
      v = option.value;
    }
    const prototype = e.tagName === "SELECT" ? HTMLSelectElement.prototype :
      e.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(e, v);
    if (e._valueTracker) e._valueTracker.setValue(old);
    e.dispatchEvent(new Event("input", {bubbles: true}));
    e.dispatchEvent(new Event("change", {bubbles: true}));
    e.dispatchEvent(new FocusEvent("blur", {bubbles: true}));
    return e.value === v;
  }
  const target = request.target || {};
  const profile = {...(request.profile || {}), ...(target.delivery_region_override || {})};
  function targetMatches(t) {
    const n = norm(t), model = norm(target.name);
    const i = n.indexOf(model);
    return !!model && i >= 0 && !/^[a-z]/.test(n.slice(i + model.length)) &&
      [target.color, target.capacity].every(v => v && n.includes(norm(v)));
  }
  function amounts(t) {
    return [...String(t || "").matchAll(/(?:RMB|[¥￥])\s*([\d,]+(?:\.\d{1,2})?)/gi)]
      .map(m => Number(m[1].replace(/,/g,""))).filter(Number.isFinite);
  }
  const controls = [...root.querySelectorAll('input[type=radio],[role=radio],button,[role=button],input[type=submit],a[data-autom]')]
    .filter(e => visible(e) || (e.type === "radio" && e.labels && [...e.labels].some(visible)))
    .map(e => {
      const raw = label(e).trim(), context = norm([e.name,e.id,e.getAttribute("data-autom"),raw].join(" "));
      const role = e.type === "radio" || e.getAttribute("role") === "radio" ? "radio" :
        e.tagName === "A" ? "link" : "button";
      let canonical = raw;
      if (role === "radio") {
        const capacity = raw.match(/(?:^|\s)(256\s*GB|512\s*GB|1\s*TB|2\s*TB)/i);
        if (capacity) canonical = capacity[1].replace(/\s/g, "").toUpperCase();
        // Apple links descriptive copy into the accessible name. Match only
        // the explicit negative option, never the paid plan or a general link.
        if (/^不加\s*AppleCare\+\s*服务计划(?:\s|你的设备|$)/i.test(raw)) canonical = "不加 AppleCare+ 服务计划";
        if (/^不折抵换购(?:\s|$)/.test(raw)) canonical = "不折抵换购";
        // Payment labels can include descriptions or use English provider IDs.
        // Do not conflate full-payment choices with installment products.
        if (!/分期|installment/.test(context)) {
          if (/支付宝|alipay/.test(context)) canonical = "支付宝";
          else if (/微信支付|wechatpay/.test(context)) canonical = "微信支付";
          else if (/applepay/.test(context)) canonical = "Apple Pay";
        }
      }
      let group = "";
      const modelChoice = role === "radio" && !/applecare|折抵|换购/i.test(raw) && raw.match(/^iPhone\s*18\s*Pro(?:\s*Max)?(?=\s|[\d（(]|$)/i);
      if (modelChoice) {canonical = /max/i.test(modelChoice[0]) ? "iPhone 18 Pro Max" : "iPhone 18 Pro"; group = "model";}
      if (role === "button" && /^编辑邮编或城市、区县/.test(norm(raw))) group = "pickup_region_edit";
      // Apple's partially reset location picker can expose the accessible name
      // "false". Restrict recovery to the checkout location section, not a
      // generic unnamed button elsewhere on the page.
      if (role === "button" && norm(raw)==="false" && e.hasAttribute('aria-expanded') &&
          /\/shop\/checkout/.test(path) && /显示此地附近的零售店/.test(ntext) &&
          /选择一个地点.*附近零售店/.test(ntext)) group = "pickup_region_edit";
      const knownInstallment = /^(花呗分期|微信分付|招商银行|中国建设银行|工商银行|中国工商银行)$/.test(raw);
      const billing = role === "radio" && (/billing|payment|付款|支付|installment/.test(context) || knownInstallment);
      const installment = billing && (/分期|installment/.test(context) || knownInstallment);
      if (knownInstallment) canonical = raw === '中国工商银行' ? '工商银行' : raw;
      const provider = /支付宝|alipay/.test(context) ? "支付宝" : /微信|wechat/.test(context) ? "微信支付" : /applepay/.test(context) ? "Apple Pay" : "";
      if (billing) group = "payment";
      const invoiceCompany = role === "radio" && /公司|企业|company|business/.test(context) && /发票|invoice/.test(context);
      if (invoiceCompany) group = "invoice";
      const termContext = /period|term|tenor|期数/i.test([e.id,e.name,e.getAttribute('data-autom')].join(' ')) || /^(1|3|6|12|24|36)\s*(?:期|个月|months)/i.test(raw.trim());
      const termMatch = role === "radio" && termContext && raw.match(/(?:^|\D)(1|3|6|12|24|36)\s*(?:期|个月|months)/i);
      const installmentMonths = termMatch ? Number(termMatch[1]) : 0;
      if (installmentMonths) group = "installment_term";
      const careOption = role === "radio" && /applecare/i.test(raw);
      const carePaid = careOption && !/^不加\s*AppleCare/i.test(raw);
      if (careOption) group = "applecare";
      if (role === "radio" && /store|retail|门店/.test(context)) group = "store";
      if (role === "radio" && scheduleKey(e)) group = scheduleKey(e);
      const storeIds = [e.value,e.id,e.getAttribute('data-store-id'),e.getAttribute('data-autom')].join(' ').match(/\bR\d{3,5}\b/g) || [];
      const wantedStore = norm(target.store).replace(/^apple/, '');
      const storeLabel = norm(raw).replace(/^apple/, '');
      const storeMatches = group === "store" && (storeIds.length && target.store_id ? storeIds.includes(target.store_id) : !!wantedStore && storeLabel === wantedStore);
      // 门店选项自带的编号（如 R683），只有唯一一个时才给出；手动开始换门店时靠它认店。
      const storeId = group === "store" && new Set(storeIds).size === 1 ? storeIds[0] : "";
      return {ref: ref(e), label: canonical, role, group, store_matches:storeMatches, store_id:storeId, provider, installment, installment_months:installmentMonths, invoice_company:invoiceCompany, care_paid:carePaid, selected: selected(e), disabled: disabled(e)};
    });
  // A date may be a radio group while its check-in slot is a native SELECT.
  // Keep independent pickers separate: one selected item is not proof that
  // all phones' required appointments have been completed.
  const pickupSchedule = [];
  const radioSchedules = new Map();
  for (const c of controls.filter(c=>/^pickup_(date|time)$/.test(c.group))) {
    const e = registry.refs.get(c.ref);
    const scope = e.name || ref(e.closest('fieldset,[role=radiogroup]') || root);
    const id = c.group + ':' + scope;
    if (!radioSchedules.has(id)) {
      const p = {ref:c.ref,id,key:c.group,kind:"radio",disabled:false,choices:[],selected:false,unavailable:false};
      radioSchedules.set(id,p); pickupSchedule.push(p);
    }
    const p = radioSchedules.get(id);
    p.choices.push({...c,value:e.value || c.label});
    p.selected = p.selected || (c.selected && !c.disabled);
  }
  for (const p of radioSchedules.values()) p.disabled = p.choices.every(c=>c.disabled);
  for (const e of [...root.querySelectorAll('select')].filter(e=>visible(e)&&scheduleKey(e))) {
    const key = scheduleKey(e);
    const choices = [...e.options].filter(o=>scheduleOption(o,key)).map(o=>({
      ref:ref(e),label:o.textContent.trim(),value:o.value,role:"option",selected:o.selected,disabled:disabled(e)
    }));
    const busy = !!e.closest('[aria-busy=true]') || (choices.length===0 && /加载|loading/i.test(e.textContent));
    pickupSchedule.push({ref:ref(e),id:key+":"+(e.name||e.id||ref(e)),key,kind:"select",disabled:disabled(e)||busy,choices,
      selected:!disabled(e)&&!busy&&choices.some(o=>o.selected)&&(!e.validity||e.validity.valid),
      unavailable:!busy && choices.length===0 && /无可用|没有可用|暂无.*时段|时段.*已满|no.*(?:slots|times).*available/i.test(e.textContent)});
  }
  const scheduleSelected = key => {
    const pickers = pickupSchedule.filter(p=>p.key===key);
    return pickers.length>0 && pickers.every(p=>p.selected);
  };
  // The installment bank search stays visible/required even with full Alipay
  // selected. Only exclude fields in a proven *other* payment-method group.
  const selectedBilling = [...root.querySelectorAll('input[type=radio]')].find(e =>
    selected(e) && /\.selectBillingOption$/i.test(e.name));
  const selectedBillingGroup = selectedBilling?.closest('.rf-paymentoptions-group');
  const inactivePaymentField = e => {
    const group = e.closest('.rf-paymentoptions-group');
    if (!/\/shop\/checkout/.test(path) || !selectedBillingGroup || !group) return false;
    if (group !== selectedBillingGroup) return true;
    // This searches additional banks; it is not a required checkout field
    // after an explicit named installment provider in this group was selected.
    // Never exclude card/account fields or an unidentified payment selection.
    return e.id==='checkout.billing.billingOptions.bankLookUp.selectBank' &&
      e.name==='selectBank' && !!e.closest('.rf-paymentoptions-typeahead-autocomplete-dropdown') &&
      /通过支付宝选择更多银行/.test(label(e)) &&
      /^(花呗分期|微信分付|招商银行|中国建设银行|工商银行|中国工商银行)$/.test(label(selectedBilling).trim());
  };
  const rawFields = [...root.querySelectorAll("input,select,textarea")].filter(e => visible(e) && (!disabled(e)||!!fieldKey(e)) && !inactivePaymentField(e) && !(e.tagName==="SELECT"&&scheduleKey(e)) &&
    (e.tagName !== "INPUT" || ["text","tel","email","number","search"].includes(e.type)));
  const fields = rawFields.map(e => {
    const key = fieldKey(e), v = String((key === "pickup_location" ? target.pickup_city : profile[key]) || "");
    return {ref: ref(e), key, configured: !!v, filled: !!e.value, matches: !!key && !!v && norm(e.value) === norm(v),
      editable: !disabled(e) && !e.readOnly,
      required: !!e.required || e.getAttribute("aria-required") === "true",
      invalid: e.getAttribute("aria-invalid") === "true" || (e.validity && !e.validity.valid)};
  });
  const errors = [...root.querySelectorAll('[role=alert],[data-autom*=error],.form-message-wrapper')]
    .some(e => visible(e) && /错误|有效|请输入|不能为空|失败|无效|incorrect|invalid|required/i.test(e.innerText || e.textContent));
  const items = [...root.querySelectorAll('li[data-autom^="bag-item-"]')].filter(visible).map(e => {
    const q = e.querySelector('[data-autom="item-quantity-dropdown"]');
    const title = e.querySelector("h2,h3,[data-autom*=product-name]");
    const remove = e.querySelector('button[data-autom="bag-item-remove-button"]');
    // 这一行带了折抵换购抵扣（「折抵」字样加一笔减去的金额），只写着可以折抵的宣传语不算。
    const tradeIn = /折抵/.test(visibleText(e)) && /[-−–]\s*(?:RMB|[¥￥])\s*[\d,]+/.test(visibleText(e));
    return {title: title ? title.textContent.trim() : "", quantity: q ? Number(q.value) : null, trade_in: tradeIn,
      quantity_ref: q ? ref(q) : "", quantity_disabled: q ? disabled(q) : true,
      quantity_options: q ? [...q.options].filter(o=>!o.disabled&&!o.parentElement.disabled&&/^\d+$/.test(o.value)).map(o=>Number(o.value)).filter(n=>n>0) : [],
      remove_ref: remove ? ref(remove) : ""};
  });
  const totalNodes = [...root.querySelectorAll('[data-autom*=total],[data-autom*=summary],[class*=total]')]
    .filter(e => visible(e) && /总计|合计|订单摘要|total/i.test(e.textContent + " " + e.getAttribute("data-autom")));
  const totals = totalNodes.flatMap(e => amounts(e.textContent));
  const total = totals.length ? Math.max(...totals) : 0;
  const productPrices = amounts((root.querySelector('[data-autom="price"]') || {}).textContent);
  const selectedCapacity = controls.find(c => c.role === "radio" && c.selected && c.label === target.capacity);
  const capacityElement = selectedCapacity && registry.refs.get(selectedCapacity.ref);
  const capacityPrices = capacityElement ? amounts(label(capacityElement)) : [];
  const product_price = productPrices.length ? Math.max(...productPrices) : capacityPrices.length ? Math.max(...capacityPrices) : 0;
  const actionLabels = controls.filter(c => !c.disabled).map(c => norm(c.label));
  const paymentChoices = controls.filter(c => c.role === "radio" &&
    (c.group === "payment" || ["支付宝","微信支付","Apple Pay"].includes(c.label)));
  const final = controls.find(c => !c.disabled && finalPattern.test(norm(c.label)));
  const headings = [...root.querySelectorAll('h1,h2,legend')].filter(visible)
    .map(e => norm(e.innerText || e.textContent)).join(" ");
  // Only an explicit visible empty-cart heading is proof of emptiness.
  // Footer/help text and an incomplete page are never sufficient.
  const emptyBagHeading = [...root.querySelectorAll('h1,h2,[data-autom="bag-empty-message"]')]
    .filter(visible).some(e => /^(?:(?:你的|您的)?(?:购物袋|购物车)(?:(?:中|内)(?:还|尚)?没有(?:任何)?商品|(?:还|尚)?(?:是)?空(?:的)?)|yourbagisempty)[。.!！]?$/.test(norm(e.textContent)));
  const paymentHeading = /如何付款|付款方式|支付方式/.test(headings);
  const orderPlaced = /订单已提交|订单已确认|订单已收到|感谢.*订购|thankyouforyourorder/.test(headings);
  const accountSignedIn = /\/shop\/account(?:\/|$)/.test(path) && [...document.querySelectorAll('a[href],button')].some(e =>
    visible(e) && /^(退出登录|退出登陆|注销|signout)$/i.test(norm(label(e))));
  let stage = "UNKNOWN";
  if (/访问被拒|accessdenied|请求过于频繁|toomanyrequests|error541/.test(ntext) || [403,429,541].includes(http_status)) stage = "BLOCKED";
  else if (/验证码|验证你是人类|captcha/.test(ntext.slice(0,1500)) || document.querySelector('iframe[src*="captcha"]')) stage = "CAPTCHA";
  else if (/page[’']?notfound|pagenotfound|thepageyou[’']relookingforcan[’']tbefound|找不到你要的页面|未找到页面|你要查找的网页找不到/.test(norm(document.title) + ntext.slice(0,1200))) stage = "NOT_FOUND";
  else if (accountSignedIn) stage = "ACCOUNT";
  else if (/\/signin/.test(path) || [...root.querySelectorAll('input[type=password]')].some(visible) ||
      [...root.querySelectorAll('iframe[src]')].some(e=>{try{const u=new URL(e.src,location.href);return visible(e)&&
        u.protocol==='https:'&&['idmsa.apple.com','appleid.apple.com','account.apple.com'].includes(u.hostname);}catch{return false;}})) stage = "LOGIN";
  else if (orderPlaced) stage = "ORDER_PLACED";
  else if (!/\/shop\/(?:checkout|account)/.test(path) &&
      !controls.some(c => c.role === "button" && !c.disabled &&
        /^(继续|结账|添加到购物袋|加入购物袋)$/.test(norm(c.label))) &&
      (/我们很快回来|我们马上回来|商店暂时关闭|商店暂不可用|we.llbeback|storeistemporarilyunavailable/.test(ntext.slice(0,1000)) ||
       (!/\/shop\/buy-iphone\//.test(path) && /来得早[，,]?来得好|即将与你见面|一会儿见|接受预购/.test(ntext.slice(0,1000))))) stage = "STORE_WAIT";
  else if (/\/shop\/account(?:\/|$)/.test(path)) stage = "ACCOUNT";
  else if (/\/shop\/bag\/?$/.test(path)) stage = "BAG";
  else if (/\/shop\/checkout/.test(path)) {
    if (final) stage = "REVIEW";
    // Billing/payment pages also contain shipping-address text and fields.
    // Visible payment choices are stronger evidence than that incidental text.
    else if (paymentChoices.length || paymentHeading) stage = "PAYMENT";
    else if (/你希望如何收到订单商品/.test(ntext)) stage = "FULFILLMENT";
    else if (/填写你的取货信息|输入你的联系信息|送货地址|收货地址/.test(ntext) && fields.some(f => f.key)) stage = "CONTACT";
    else if (/如何付款|付款方式|支付方式/.test(ntext)) stage = "PAYMENT";
  } else if (/\/shop\/buy-iphone\//.test(path)) {
    if (actionLabels.includes("查看购物袋")) stage = "ATTACH";
    // Before launch Apple can expose the full configurator while keeping
    // Continue disabled. Selectable radios alone are not proof of sale.
    // An enabled purchase action takes precedence over leftover launch copy.
    else if (/接受预购|即将发售|即将推出|暂未发售/.test(ntext.slice(0,1000)) &&
             !controls.some(c => c.role === "button" && !c.disabled &&
               /^(继续|添加到购物袋|加入购物袋)$/.test(norm(c.label)))) stage = "WAIT_RELEASE";
    else if (controls.some(c => c.role === "radio" && !c.disabled)) stage = "PRODUCT";
    else if (/接受预购|即将发售|即将推出|暂未发售/.test(ntext.slice(0,1000))) stage = "WAIT_RELEASE";
  }
  // Official maintenance/access-denied redirects must not be hidden by the
  // navigation guard. Unexpected purchasable pages still cannot be adopted.
  if (expectedPathMismatch && !["BLOCKED","CAPTCHA","NOT_FOUND","LOGIN","STORE_WAIT"].includes(stage)) return {stage:"LOADING"};
  const choose = label => controls.some(c => norm(c.label) === norm(label) && c.selected);
  // Read visible checkout notices wherever rendered (including portal dialogs),
  // not only the first characters of the page. Never interpret FAQ/upsell copy,
  // grey buttons or a network spinner as stock evidence.
  const checkoutPath = /\/shop\/(?:checkout|bag)(?:\/|$)/.test(path);
  const stockMessages = checkoutPath ? [...document.querySelectorAll(
    '[role=alert],[role=status],[role=dialog],dialog[open],[data-autom*=error],.form-message-wrapper,p,h1,h2,h3')]
    .filter(e=>visible(e) && !e.closest('footer,nav,aside,[data-autom*=faq],[data-autom*=recommend],details:not([open])'))
    .filter(e=>root.contains(e)||e.closest('[role=dialog],dialog[open],[role=alert]'))
    .filter(e=>{
      const row=e.closest('li[data-autom^="bag-item-"]');
      return !row || targetMatches((row.querySelector('h2,h3,[data-autom*=product-name]')||{}).textContent);
    }).map(e=>norm(e.innerText||e.textContent)).filter(t=>t.length>0&&t.length<600&&
      !/如果|假如|若遇|若.*无货|如遇|一旦|缺货时|可能.*(?:缺货|无货)|验证码|验证你|登录|同意.*条款|接受.*协议/.test(t)) : [];
  function stockMessageKind(t) {
    if (/^(?:很抱歉[,，。！!]?|抱歉[,，。！!]?|对不起[,，。！!]?)*(?:此|该|你所选的|您所选的|所选|你选择的|您选择的|购物袋中(?:的)?|订单中(?:的)?).{0,60}(?:商品|产品|配置|机型).{0,35}(?:已售罄|已无货|暂时无货|暂无库存|库存不足|不再有货|目前不可用|不再可用)/.test(t) ||
        /^(?:很抱歉[,，。！!]?|抱歉[,，。！!]?)*(?:商品已售罄|库存不足|商品库存不足|所需数量超出可用库存|所选数量超过(?:当前)?可用库存)[。！!，,]?$/.test(t)) return "all";
    if (/^(?:很抱歉[,，。！!]?|抱歉[,，。！!]?)*(?:此|该|所选|你选择的|您选择的|购物袋中(?:的)?|订单中(?:的)?).{0,60}(?:商品|产品|配置|订单).{0,35}(?:无法送货|不可送货|无法配送|不支持配送|配送不可用)/.test(t)) return "delivery";
    if (/^(?:很抱歉[,，。！!]?|抱歉[,，。！!]?)*(?:此|该|所选|你选择的|您选择的).{0,60}(?:商品|产品|配置|订单|零售店|门店).{0,35}(?:无法取货|不可取货|无法自提|无货可取|不支持取货)/.test(t)) return "pickup";
    return "";
  }
  const protectedDialog = [...document.querySelectorAll('[role=dialog],dialog[open]')].some(e=>visible(e)&&
    /验证码|验证你|登录|同意.*条款|接受.*协议/.test(norm(e.innerText||e.textContent)));
  const stockKinds = protectedDialog ? [] : stockMessages.map(stockMessageKind).filter(Boolean);
  const stockIssue = stockKinds.includes('all') ? 'all' :
    stockKinds.includes('delivery') && (choose('为我送货')||['CONTACT','PAYMENT','REVIEW'].includes(stage)) ? 'delivery' :
    stockKinds.includes('pickup') && (choose('我要取货')||['CONTACT','PAYMENT','REVIEW'].includes(stage)) ? 'pickup' : '';
  const dedicatedReview = [...root.querySelectorAll('[data-autom="order-summary"],[data-autom="review-order"],.rs-checkout-review')].find(visible);
  // Current CN review uses the checkout main without the older wrapper.
  // Both an explicit review heading and a final-order action are required.
  const review = dedicatedReview || (stage === "REVIEW" && final &&
    /准备下单了吗.*请确保以下信息均准确无误/.test(headings) ? root : null);
  const reviewText = visibleText(review);
  // Payment provider can be an image on Review, with no radio buttons.
  // Read only the payment section, never the whole-page marketing/FAQ copy.
  let reviewPayment = null;
  let reviewFullPayment = '';
  if (review) {
    const hs = [...review.querySelectorAll('h2')].filter(visible);
    const h = hs.find(e=>norm(e.textContent)==='付款详情');
    if (h) {
      const end = hs[hs.indexOf(h)+1];
      const between = e => !!(h.compareDocumentPosition(e)&Node.DOCUMENT_POSITION_FOLLOWING) &&
        (!end || !!(e.compareDocumentPosition(end)&Node.DOCUMENT_POSITION_FOLLOWING));
      const walker = document.createTreeWalker(review,NodeFilter.SHOW_TEXT), texts=[];
      while(walker.nextNode()) if(visible(walker.currentNode.parentElement)&&between(walker.currentNode)) texts.push(walker.currentNode.textContent);
      const labels=[...review.querySelectorAll('img')].filter(e=>visible(e)&&between(e)).map(e=>e.alt);
      const pt=texts.join(' ')+' '+labels.join(' ');
      if(!/分期|个月|每月|每期|分付/.test(pt)) {
        const full=['支付宝','微信支付'].filter(x=>pt.includes(x));
        if(full.length===1)reviewFullPayment=full[0];
      }
      const providers=['花呗分期','微信分付','招商银行','中国建设银行','工商银行'].filter(x=>pt.includes(x));
      const months=[...pt.matchAll(/(?:^|\D)(1|3|6|12|24|36)\s*(?:期|个月)/g)].map(m=>Number(m[1]));
      const fees=[...reviewText.matchAll(/收取\s*([\d.]+)%\s*手续费/g)].map(m=>Number(m[1]));
      if(providers.length===1 && months.length && new Set(months).size===1 && fees.length===1)
        reviewPayment={provider:providers[0],months:months[0],fee_percent:fees[0]};
    }
  }
  const selectedTerms = [...root.querySelectorAll('select')].filter(e=>
    visible(e) && !disabled(e) && !inactivePaymentField(e) && !!e.value && e.selectedIndex >= 0 && !e.options[e.selectedIndex].disabled &&
    /installment|分期|期数|term|tenor/i.test([e.id,e.name,label(e)].join(' ')));
  const selectedMonths = controls.filter(c=>c.installment_months && c.selected && !c.disabled && !inactivePaymentField(registry.refs.get(c.ref))).map(c=>c.installment_months)
    .concat(selectedTerms.map(e=>e.options[e.selectedIndex].textContent.match(/(?:^|\D)(1|3|6|12|24|36)\s*(?:期|个月|months)/i)).filter(Boolean).map(m=>Number(m[1])));
  const installmentState=JSON.stringify(controls.filter(c=>['payment','installment_term'].includes(c.group)).map(c=>[c.label,c.selected,c.disabled]));
  if(registry.installmentState!==installmentState){registry.installmentState=installmentState;registry.installmentSince=Date.now();}
  const activeInstallments=controls.filter(c=>c.group==='payment'&&c.installment&&c.selected&&!c.disabled);
  const installmentStable=activeInstallments.length===1 && controls.some(c=>c.group==='installment_term'&&!c.disabled) &&
    Date.now()-registry.installmentSince>=1500 && !root.querySelector('[aria-busy="true"]');
  const reviewQuantities = [...reviewText.matchAll(/数量\s*[:：]?\s*(\d+)/g)];
  // A global first match may belong to a different line item. Ambiguity fails closed.
  const reviewQuantity = reviewQuantities.length === 1 ? reviewQuantities[0] : null;
  const reviewPhoneHeadings = review ? [...review.querySelectorAll('h2,h3,[data-autom*=product-name]')].filter(e=>
    visible(e) && /^iphone(?:duo|air|se|\d)/i.test(norm(e.textContent)) && !/applecare/i.test(e.textContent) &&
    ![...e.querySelectorAll('h2,h3,[data-autom*=product-name]')].some(c=>/^iphone/i.test(norm(c.textContent)))) : [];
  const reviewPhoneRows = reviewPhoneHeadings.map(h=>{
    if (!targetMatches(h.textContent)) return null;
    let candidate=null;
    for(let row=h.parentElement;row && review.contains(row);row=row.parentElement){
      if(reviewPhoneHeadings.filter(other=>row.contains(other)).length!==1) break;
      const qs=[...visibleText(row).matchAll(/数量\s*[:：]?\s*(\d+)/g)];
      if(qs.length===1) candidate={row,quantity:Number(qs[0][1])};
      if(row.matches('li,[data-autom*=item]') && candidate) break;
    }
    return candidate;
  });
  const reviewPhoneQuantity = reviewPhoneRows.length && reviewPhoneRows.every(x=>x&&[1,2].includes(x.quantity))
    ? reviewPhoneRows.reduce((sum,x)=>sum+x.quantity,0) : null;
  // Evidence is scoped to each phone, never to the union of the whole order.
  const dateTokens = t => t.match(/20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}\s*月\s*\d{1,2}\s*日|今天|明天|后天/g)||[];
  const reviewItems = reviewPhoneRows.map(x=>{
    if(!x)return {target_matches:false};
    const t=visibleText(x.row);
    const pickup=/取货|自提/.test(t),delivery=/送货|配送/.test(t);
    const stores=(target.store_names||[target.store]).filter(name=>name&&norm(t).includes(norm(name)));
    return {target_matches:true,quantity:x.quantity,method:pickup&&!delivery?'pickup':delivery&&!pickup?'delivery':'',
      store_matches:stores.length===1&&norm(stores[0])===norm(target.store),pickup_dates:dateTokens(t)};
  });
  // CN can place the pickup summary outside the item row and explicitly defer
  // the final date until payment. Expose that fact without inventing a store
  // or date. Only the stateful runner may accept it for an already-verified,
  // single-item earliest-date pickup in this same attempt.
  const deferredSections = review ? [...review.querySelectorAll('.rs-review-fulfillment')].filter(visible) : [];
  const detailHeadings = review ? [...review.querySelectorAll('.rs-review-details h2')].filter(visible).map(e=>norm(e.textContent)) : [];
  const reviewStoreNames = (target.store_names||[target.store]).filter(name=>name&&norm(reviewText).includes(norm(name)));
  const deferredPickup = stage==='REVIEW' && deferredSections.length===1 &&
    /取货日期待付款完成后确定/.test(norm(visibleText(deferredSections[0]))) &&
    detailHeadings.filter(t=>t==='取货详情').length===1 &&
    !detailHeadings.some(t=>/送货详情|配送详情/.test(t)) &&
    reviewPhoneHeadings.length===1 && reviewItems.length===1 && reviewItems[0].target_matches &&
    reviewItems[0].quantity===1 && target.quantity===1 && !!target.store && !!target.store_id &&
    reviewItems[0].method!=='delivery' && reviewItems[0].pickup_dates.length===0 &&
    reviewStoreNames.every(name=>norm(name)===norm(target.store));
  if(deferredPickup && !reviewItems[0].method)reviewItems[0].method='pickup';
  const reviewProductHeadings = review ? [...review.querySelectorAll('h2,h3,[data-autom*=product-name]')].filter(e=>visible(e)&&
    ![...e.querySelectorAll('h2,h3,[data-autom*=product-name]')].length&&
    !!e.closest('li,[data-autom*=item]')) : [];
  const extraHeadings=reviewProductHeadings.filter(e=>!/^iphone(?:duo|air|se|\d)/i.test(norm(e.textContent))&&!/applecare/i.test(e.textContent));
  const extrasMatch=!!review&&extraHeadings.length===(target.extras||[]).length&&(target.extras||[]).every(item=>
    extraHeadings.filter(e=>norm(e.textContent)===norm(item.title)&&
      [...(e.closest('li,[data-autom*=item]').textContent).matchAll(/数量\s*[:：]?\s*(\d+)/g)].length===1&&
      Number((e.closest('li,[data-autom*=item]').textContent.match(/数量\s*[:：]?\s*(\d+)/)||[])[1])===item.quantity).length===1);
  const selectedStores=controls.filter(c=>c.group==='store'&&c.selected);
  const allStoresSelected=selectedStores.length>0&&selectedStores.every(c=>c.store_matches&&!c.disabled)&&
    controls.filter(c=>c.group==='store').every(c=>{
      const e=registry.refs.get(c.ref),scope=e.name||ref(e.closest('fieldset,[role=radiogroup]')||root);
      return selectedStores.some(s=>{const se=registry.refs.get(s.ref);return (se.name||ref(se.closest('fieldset,[role=radiogroup]')||root))===scope;});
    });
  const modelHeading = (root.querySelector("h1") || {}).textContent || "";
  const selectedModels = controls.filter(c=>c.group==="model"&&c.selected&&!c.disabled);
  const product_model = selectedModels.length===1 ? selectedModels[0].label :
    (modelHeading.match(/iPhone\s*(?:Duo|17(?:\s*Pro(?:\s*Max)?|e)?|18\s*Pro(?:\s*Max)?)/i) || [""])[0];
  const targetModelSelected = !controls.some(c=>c.group==="model") ||
    (selectedModels.length===1 && norm(product_model)===norm(target.name));
  const choiceUnavailable = choice => controls.some(c => {
    if(c.role!=="radio" || !c.disabled)return false;
    const e=registry.refs.get(c.ref),raw=label(e),n=norm(raw),wanted=norm(choice);
    const unavailable=/暂时无货|暂无库存|已售罄|无货可订购|不可购买|outofstock|soldout/.test(n) ||
      /^(?:out.of.stock|sold.out|unavailable)$/.test(e.getAttribute('data-availability')||'');
    // Disabled choices also occur while changing model/color. A disabled
    // radio alone (even with enabled siblings) is never proof of no stock.
    return unavailable && (norm(c.label)===wanted || n.startsWith(wanted));
  });
  // Coverage belongs to the target phone row. An upsell's price/title or the
  // phone's own Remove button is never proof that coverage was purchased.
  const careRows = [...root.querySelectorAll('li[data-autom^="bag-item-"]')].filter(e =>
    visible(e) && targetMatches((e.querySelector('h2,h3,[data-autom*=product-name]') || {}).textContent || ""));
  let careEvidence = 0;
  let careAbsentRows = 0;
  const bagCareActions = new Map();
  if (stage === "BAG") for (const row of careRows) {
    const evidenceBefore = careEvidence;
    let careAbsent = false;
    const actions = [...row.querySelectorAll('button,a,[role=button]')].filter(visible);
    for (const e of actions) {
      const t=norm(label(e));
      if (!/applecare/i.test(t) || /了解|比较|详情|learn|compare/.test(t)) continue;
      if (/^(添加|加购|加入|add)/i.test(t) && !disabled(e)) {
        careAbsent = true;
        const prices=[...new Set(amounts(label(e)).filter(n=>n>0))];
        if(prices.length===1)bagCareActions.set(ref(e),{ref:ref(e),kind:"add",price:prices[0]});
      }
      if (/移除|删除|remove/i.test(t) && !/^(添加|加购|加入|add)/i.test(t)) {
        careEvidence++;
        if(!disabled(e))bagCareActions.set(ref(e),{ref:ref(e),kind:"remove"});
      }
    }
    if ([...row.querySelectorAll('p,span,h3,h4')].filter(visible).some(e => {
      const t = norm(e.textContent);
      return t.length < 160 && !/未添加|尚未|不包含|notincluded/.test(t) &&
        /(?:已添加|已包含|包含了).*applecare|applecare.*(?:已添加|已包含)/i.test(t);
    })) careEvidence++;
    if ([...row.querySelectorAll('p,span,h3,h4')].filter(visible).some(e => {
      const t = norm(e.textContent);
      return /^(?:(?:未添加|尚未添加|不包含)applecare\+?(?:服务计划|保障计划)?|applecare\+?(?:服务计划|保障计划)?(?:未添加|尚未添加|notincluded))[。.!！]?$/.test(t);
    })) careAbsent = true;
    for (const action of actions.filter(e => /^(移除|删除|remove)$/i.test(norm(label(e))))) {
      // The nearest coverage section can use a plain "移除" link. Require a
      // coverage heading and a price, and never climb into the phone row.
      for (let section=action.parentElement; section && section!==row; section=section.parentElement) {
        if (section.querySelector('[data-autom="item-quantity-dropdown"]')) break;
        const headings=[...section.querySelectorAll('h2,h3,h4,[data-autom*=title],[data-autom*=product-name]')].filter(visible);
        const careTitle=headings.some(e=>/applecare/i.test(e.textContent));
        const otherTitle=headings.some(e=>/iphone|airpods|ipad|macbook/i.test(e.textContent) && !/applecare/i.test(e.textContent));
        const offer= [...section.querySelectorAll('button,a,[role=button]')].filter(visible).some(e=>/^(添加|加购|加入|add)(?!.*已)/i.test(norm(label(e))));
        if (careTitle && !otherTitle && !offer && amounts(section.textContent).some(n=>n>0)) {careEvidence++;if(!disabled(action))bagCareActions.set(ref(action),{ref:ref(action),kind:"remove"});break;}
      }
    }
    const quantityControl = row.querySelector('[data-autom="item-quantity-dropdown"]');
    const rowItem = quantityControl && items.find(i=>i.quantity_ref===ref(quantityControl));
    const included = careEvidence > evidenceBefore;
    if (!included && careAbsent) careAbsentRows++;
    if (rowItem) rowItem.applecare_included = included ? true : careAbsent ? false : null;
  }
  const regionEditors = controls.filter(c=>c.group==="pickup_region_edit");
  const regionOpen = stage==="FULFILLMENT" && regionEditors.length===1 &&
    registry.refs.get(regionEditors[0].ref).getAttribute("aria-expanded")==="true";
  const regionTabs = regionOpen ? [...root.querySelectorAll('[role=tab]')].filter(e=>visible(e)&&selected(e)) : [];
  const regionLabel = regionTabs.length===1 ? norm(label(regionTabs[0])) : "";
  const regionStep = /省[/／]市[/／]自治区$/.test(regionLabel) ? "province" :
    /城镇[/／]城市$/.test(regionLabel) ? "city" : /^区$|区$/.test(regionLabel) ? "district" : "";
  // One read/compare/change/read-back protocol for both destinations. Never
  // infer success from a dispatched click, nor use the delivery address for pickup.
  const regionKeys = ["province", "city", "district"];
  const regionContext = choose("我要取货") || (stage === "CONTACT" && target.fulfillment === "pickup") ? "pickup" : "delivery";
  const regionTarget = regionContext === "pickup" ? (target.pickup_region || {}) : (target.delivery_region_override || profile);
  const provinceAliases={"内蒙古自治区":"内蒙古","广西壮族自治区":"广西","西藏自治区":"西藏","宁夏回族自治区":"宁夏","新疆维吾尔自治区":"新疆"};
  const regionNorm = (v, key) => key === "province" ? (provinceAliases[norm(v)] || norm(v).replace(/[省市]$/, "")) : norm(v).replace(key === "city" ? /市$/ : /$^/, "");
  const regionEqual = (a,b,key) => !!b && regionNorm(a,key) === regionNorm(b,key);
  const municipality = ['北京','上海','天津','重庆'].includes(norm(regionTarget.province).replace(/市$/,'')) &&
    norm(regionTarget.province).replace(/市$/,'')===norm(regionTarget.city).replace(/市$/,'');
  const tabKey = e => {
    const t=norm(label(e));
    return /省[/／]市[/／]自治区$|省份$/.test(t) ? "province" :
      /城镇[/／]城市$|城市$/.test(t) ? "city" : /区县$|区$/.test(t) ? "district" : "";
  };
  const tabValue = e => label(e).replace(/(?:省[/／]市[/／]自治区|城镇[/／]城市|省份|城市|区县|区)\s*$/, "").trim();
  let regionPicker = null;
  let committedRegion = null;
  const manualElements = new Set();
  const manualResetElements = new Set();
  const regionActions = new Map();
  const regionAction = (e, step, operation) => {
    const a={ref:ref(e),key:"region_"+regionContext+"_"+step};
    regionActions.set(a.key,{...a,element:e,operation,step});
    return a;
  };
  if (["FULFILLMENT","CONTACT"].includes(stage)) {
    if (regionEditors.length) {
      const editor=regionEditors.length===1 ? registry.refs.get(regionEditors[0].ref) : null;
      const open=!!editor && editor.getAttribute("aria-expanded")==="true";
      const parts=editor ? label(editor).replace(/^编辑邮编或城市、区县\s*[,，:]?\s*/,"").trim().split(/\s+/) : [];
      if(editor)manualResetElements.add(editor);
      if(!open && parts.length===3)committedRegion=Object.fromEntries(regionKeys.map((k,i)=>[k,parts[i]]));
      if(!open && parts.length===2 && ['北京','上海','天津','重庆'].includes(norm(parts[0]).replace(/市$/,'')))committedRegion={province:parts[0],city:parts[0],district:parts[1]};
      const matched=(parts.length===3 && regionKeys.every((k,i)=>regionEqual(parts[i],regionTarget[k],k))) ||
        (municipality && parts.length===2 && regionEqual(parts[0],regionTarget.city,'city') && regionEqual(parts[1],regionTarget.district,'district'));
      regionPicker={context:regionContext,kind:"tabs",open,matched,configured:regionKeys.every(k=>!!regionTarget[k]),next:null,reason:"loading"};
      if (!editor) regionPicker.reason="ambiguous";
      else if (!open) {
        if (!matched && !disabled(editor)) regionPicker.next=regionAction(editor,"open","click");
      } else {
        const tabs=[...root.querySelectorAll('[role=tab]')].filter(e=>visible(e)&&tabKey(e));
        tabs.filter(e=>selected(e)&&tabKey(e)==='district').forEach(e=>{const p=document.getElementById(e.getAttribute('aria-controls'));if(p)manualElements.add(p);});
        tabs.forEach(e=>{manualResetElements.add(e);if(selected(e)&&tabKey(e)!=='district'){const p=document.getElementById(e.getAttribute('aria-controls'));if(p)manualResetElements.add(p);}});
        const active=tabs.filter(selected);
        const byKey=k=>tabs.filter(e=>tabKey(e)===k);
        if (active.length!==1 || regionKeys.some(k=>byKey(k).length>1)) regionPicker.reason="ambiguous";
        else {
          const step=tabKey(active[0]);
          regionPicker.step=step;
          regionPicker.steps=regionKeys.map(k=>({key:k,matches:
            (byKey(k).length===1&&regionEqual(tabValue(byKey(k)[0]),regionTarget[k],k)) ||
            (k==='city'&&municipality&&byKey(k).length===0&&byKey('district').length===1&&byKey('province').length===1&&regionEqual(tabValue(byKey('province')[0]),regionTarget.province,'province'))}));
          // Breadcrumb VALUES can remain at the saved Wuhan location while
          // the active panel already contains Shanghai districts. The active
          // layer/options determine the next choice; only the committed editor
          // summary and exact store ID establish destination success.
          const nextKey=step==='province' ? (municipality ? 'district' : 'city') : step==='city' ? 'district' : '';
          const nextTab=byKey(nextKey)[0];
          const same=regionEqual(tabValue(active[0]),regionTarget[step],step);
          if (same && nextTab && !disabled(nextTab)) regionPicker.next=regionAction(nextTab,"tab_"+nextKey,"click");
          else {
            // A matching breadcrumb can be Apple's prefilled/default label,
            // not a committed selection. If no child tab exists, select the
            // exact option even when the current label already matches.
            const panelId=active[0].getAttribute("aria-controls");
            const panel=panelId ? document.getElementById(panelId) : root;
            const options=panel && visible(panel) ? [...panel.querySelectorAll('button,[role=button],[role=option]')].filter(e=>visible(e)&&!disabled(e)&&regionEqual(label(e),regionTarget[step],step)) : [];
            // 这一层找不到目标时，先看上层：省份（非直辖市还有城市）和目标不一致，说明这一层列的是
            // 别处的选项（比如官网默认定位到南京），点回上层重新选，而不是当作缺失交给本人。
            const parentDiffers=k=>k!==step&&byKey(k).length===1&&!regionEqual(tabValue(byKey(k)[0]),regionTarget[k],k);
            const upKey=step==='province'?'':parentDiffers('province')?'province':
              (step==='district'&&!municipality&&parentDiffers('city'))?'city':'';
            const upTab=upKey?byKey(upKey)[0]:null;
            if (options.length===1) regionPicker.next=regionAction(options[0],step,"click");
            else if (options.length>1) regionPicker.reason="ambiguous";
            else if (upTab && !disabled(upTab)) regionPicker.next=regionAction(upTab,"tab_"+upKey,"click");
            else if(panel && !panel.querySelector('[aria-busy="true"],[role="progressbar"]') && [...panel.querySelectorAll('button,[role=button],[role=option]')].some(e=>visible(e)&&!disabled(e)&&!tabKey(e)))regionPicker.reason="missing";
            else if (step!=='province' && byKey('province').length===1 && !disabled(byKey('province')[0]))
              regionPicker.recovery=regionAction(byKey('province')[0],'tab_province','click');
          }
          // The engine's per-level read-back grace prevents repeated selection
          // while a dispatched choice is loading its dependent child panel.
        }
        // Never close a loading editor as a recovery action. Apple commits each
        // dependent level asynchronously; closing cancels/races that transition.
      }
    } else {
      const regionFields=[...root.querySelectorAll('select,input')].filter(e=>visible(e)&&regionKeys.includes(fieldKey(e)));
      if (regionFields.length) {
        regionFields.filter(e=>fieldKey(e)==='district').forEach(e=>manualElements.add(e));
        regionFields.filter(e=>fieldKey(e)!=='district').forEach(e=>manualResetElements.add(e));
        const states=regionKeys.map(k=>{
          const es=regionFields.filter(e=>fieldKey(e)===k),e=es[0];
          const current=e?.tagName==='SELECT' ? e.selectedOptions[0]?.textContent : e?.value;
          return {key:k,element:e,count:es.length,matches:es.length===1&&regionEqual(current,regionTarget[k],k)};
        });
        const cityState=states.find(s=>s.key==='city');
        if(municipality && cityState.count===0 && states.find(s=>s.key==='province').matches && states.find(s=>s.key==='district').count===1)
          cityState.matches=true;
        const values={};
        for(const st of states){const e=st.element;if(st.count===1&&e?.tagName==='SELECT'&&!disabled(e)&&e.value&&e.checkValidity()&&!/请选择|选择|加载/.test(e.selectedOptions[0]?.textContent||''))values[st.key]=e.selectedOptions[0].textContent.trim();}
        if(!values.city && values.province && ['北京','上海','天津','重庆'].includes(norm(values.province).replace(/市$/,'')) && cityState.count===0)values.city=values.province;
        if(regionKeys.every(k=>values[k]))committedRegion=values;
        regionPicker={context:regionContext,kind:"fields",open:true,configured:regionKeys.every(k=>!!regionTarget[k]),matched:states.every(s=>s.matches),next:null,reason:"loading"};
        const mismatch=states.find(s=>!s.matches);
        if (states.some(s=>s.count>1)) regionPicker.reason="ambiguous";
        else if (mismatch?.element && !disabled(mismatch.element) && !mismatch.element.readOnly) {
          const e=mismatch.element,k=mismatch.key;
          if (e.tagName==='SELECT') {
            const options=[...e.options].filter(o=>!o.disabled&&!o.parentElement.disabled&&regionEqual(o.textContent,regionTarget[k],k));
            if (options.length===1) {
              regionPicker.next=regionAction(e,k,"value");
              regionActions.get(regionPicker.next.key).value=options[0].value;
            } else if(options.length>1) regionPicker.reason="ambiguous";
            else if([...e.options].some(o=>o.value&&!o.disabled&&!/请选择|选择|加载/.test(o.textContent)))regionPicker.reason="missing";
          } else {
            regionPicker.next=regionAction(e,k,"value");
            regionActions.get(regionPicker.next.key).value=regionTarget[k];
          }
        }
      }
    }
  }
  // Only explicit trusted interaction in a region control can approve a
  // different region. Programmatic changes and pre-existing defaults cannot.
  if(target.manual_region_token){
    if(registry.manualRegion?.token!==target.manual_region_token)registry.manualRegion={token:target.manual_region_token,touched:false,elements:new Set()};
    registry.manualRegion.elements=manualElements;
    registry.manualRegion.resetElements=manualResetElements;
    if(!registry.manualRegionListener){
      registry.manualRegionListener=e=>{const m=registry.manualRegion;const n=e.target?.closest?.('select,button,[role=button],[role=option],[role=tab]');if(!e.isTrusted||!m||!n)return;const path=e.composedPath();if(path.some(n=>m.resetElements.has(n)))m.touched=false;if(n.getAttribute('role')!=='tab'&&((e.type==='change'&&n.tagName==='SELECT')||(e.type==='click'&&n.tagName!=='SELECT'))&&path.some(n=>m.elements.has(n)))m.touched=true;};
      for(const event of ['change','click'])document.addEventListener(event,registry.manualRegionListener,true);
    }
  }else registry.manualRegion=null;
  if(regionPicker){
    const m=registry.manualRegion;
    const regionInvalid=fields.some(f=>regionKeys.includes(f.key)&&f.invalid)||[...root.querySelectorAll('[role=alert],[data-autom*=error],.form-message-wrapper')].some(e=>visible(e)&&/省份|城市|区县|地区/.test(e.textContent)&&/错误|有效|请输入|不能为空|失败|无效|选择/.test(e.textContent));
    regionPicker.manual_token=m?.token||'';
    regionPicker.manual_touched=!!m?.touched;
    regionPicker.manual_ready=!!committedRegion&&!regionInvalid&&(!regionPicker.open||regionPicker.kind==='fields')&&root.getAttribute('aria-busy')!=='true'&&![...root.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible);
    // Region values are returned only during manual handoff, never in logs.
    if(target.manual_region_token&&regionPicker.manual_ready)regionPicker.manual_values=committedRegion;
  }
  const privacyConsentRequired = [...document.querySelectorAll('[role=dialog],dialog[open]')].some(e=>visible(e)&&/apple和你的数据隐私/.test(norm(e.textContent)));
  const contactVerificationRequired = stage==='CONTACT' && [...root.querySelectorAll('button[data-autom^="contactVerify-button-"]')].some(e=>visible(e)&&!disabled(e)&&norm(label(e))==='验证');
  const reviewEditRefs = {};
  if(stage==='REVIEW' && review){
    for(const [key,autom] of Object.entries({fulfillment:'changeShippingMethod',contact:'pickup-changeLink',payment:'billing-changeLink'})){
      const edits=[...review.querySelectorAll(`[data-autom="${autom}"]`)].filter(e=>visible(e)&&!disabled(e)&&!forbidden(e));
      if(edits.length===1 && controls.some(c=>c.ref===ref(edits[0])))reviewEditRefs[key]=ref(edits[0]);
    }
  }
  const out = {stage, http_status, controls, fields, errors, items, total, product_price, product_model,
    review_edit_refs: reviewEditRefs,
    stock_issue: stockIssue,
    contact_verification_required:contactVerificationRequired,
    pickup_schedule: stage==="FULFILLMENT" ? pickupSchedule : [],
    bag_busy: [...root.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible),
    pickup_region_open:regionOpen, pickup_region_step:regionStep, region_picker:regionPicker,
    bag_applecare: careEvidence > 0 ? "included" :
      careRows.length > 0 && careAbsentRows === careRows.length ? "none" : "unconfirmed",
    bag_care_actions: careRows.length===1 ? [...bagCareActions.values()] : [],
    trade_in_selected: controls.some(c=>c.role==="radio" && c.selected && /折抵|换购/.test(c.label) && !/^不折抵/.test(c.label) && !/年年焕新/.test(c.label)),
    diagnostics: {privacy_consent_required:privacyConsentRequired,contact_verification_required:contactVerificationRequired,bag_care_evidence_count:careEvidence, bag_target_row_count:careRows.length,
      product_care_count:controls.filter(c=>c.group==="applecare").length,
      product_care_disabled_count:controls.filter(c=>c.group==="applecare"&&c.disabled).length,
      product_care_paid_count:controls.filter(c=>c.group==="applecare"&&c.care_paid).length,
      pickup_method_count:controls.filter(c=>norm(c.label)===norm("我要取货")).length,
      pickup_method_disabled_count:controls.filter(c=>norm(c.label)===norm("我要取货")&&c.disabled).length,
      pickup_method_selected:choose("我要取货"),
      delivery_method_count:controls.filter(c=>norm(c.label)===norm("为我送货")).length,
      delivery_method_selected:choose("为我送货"),
      target_store_count:controls.filter(c=>c.group==="store"&&c.store_matches).length,
      target_store_selected:controls.some(c=>c.group==="store"&&c.store_matches&&c.selected),
      pickup_date_count:pickupSchedule.filter(p=>p.key==="pickup_date").reduce((n,p)=>n+p.choices.filter(c=>!c.disabled).length,0),
      pickup_time_count:pickupSchedule.filter(p=>p.key==="pickup_time").reduce((n,p)=>n+p.choices.filter(c=>!c.disabled).length,0),
      payment_heading:paymentHeading, payment_choice_count:paymentChoices.length,
      visible_control_count:controls.length, disabled_control_count:controls.filter(c=>c.disabled).length,
      unknown_required_count:fields.filter(f=>!f.key && f.required).length,
      invalid_field_count:fields.filter(f=>f.invalid).length,
      visible_dialog:!![...document.querySelectorAll('[role=dialog],dialog[open]')].find(visible),
      visible_iframe_count:[...document.querySelectorAll('iframe')].filter(visible).length,
      login_spinner:stage === "LOGIN" && !![...root.querySelectorAll('[role=progressbar],[aria-busy=true],[class*=spinner],[class*=loading-indicator],[class*=loadingIndicator]')].find(visible)},
    payment_choices_present: paymentChoices.length > 0,
    // Only selected installment controls count, never marketing copy.
    installment_terms_selected: selectedMonths.length > 0 && new Set(selectedMonths).size === 1,
    installment_selected_months: selectedMonths.length > 0 && new Set(selectedMonths).size === 1 ? selectedMonths[0] : null,
    authenticated_account: stage === "ACCOUNT" && accountSignedIn,
    variant_unavailable: stage === "PRODUCT" && targetModelSelected && (choiceUnavailable(target.color) ||
      (controls.some(c=>c.label===target.color && c.selected) && choiceUnavailable(target.capacity) &&
       controls.some(c=>c.role==="radio" && /^(256GB|512GB|1TB|2TB)$/.test(c.label) && !c.disabled))),
    purchasable: stage === "PRODUCT" && targetModelSelected && !/暂时无货|售罄/.test(ntext.slice(0,1200)),
    sold_out: checkoutPath ? stockIssue==='all' : stage !== "WAIT_RELEASE" && (stage !== "PRODUCT" || targetModelSelected) && /该商品已售罄|此配置暂时无货|此商品目前不可用|所选商品已售罄|所选配置暂无库存|你选择的商品已无货|购物袋中的商品已无货/.test(ntext.slice(0,1800)),
    delivery_selected: choose("为我送货"), pickup_selected: choose("我要取货"),
    delivery_unavailable: stockIssue==='delivery',
    pickup_unavailable: (stage === "FULFILLMENT" && choose("我要取货") &&
      /你的订单仅可使用送货服务/.test(headings)) ||
      controls.some(c=>c.group === "store" && c.store_matches && c.disabled) ||
      (/你所在的地区无货可取/.test(ntext.slice(0,2500)) && fields.some(f=>f.key === "pickup_location" && f.matches)),
    store_selected: allStoresSelected,
    store_selection_conflict: selectedStores.some(c=>!c.store_matches),
    pickup_date_selected: scheduleSelected("pickup_date"),
    pickup_time_selected: scheduleSelected("pickup_time"),
    // Some official pickup flows show availability (today, tomorrow or a date)
    // on the selected store but no appointment picker. Do not require "today".
    // Only the explicit enabled pickup-details action
    // permits advancing; never infer an appointment from a generic Continue.
    pickup_schedule_not_required: stage === "FULFILLMENT" && choose("我要取货") && !regionOpen &&
      controls.some(c => c.group === "store" && c.store_matches && c.selected && !c.disabled &&
        /可取货/.test(norm(c.label)) && !/不可取货|无法取货|暂不可|不能取货|无货/.test(norm(c.label))) &&
      pickupSchedule.length===0 && !fields.some(f=>f.required&&!f.key) &&
      controls.filter(c => c.role === "button" && norm(c.label) === norm("继续填写取货详情") && !c.disabled).length === 1,
    final_button: !!final, final_order_ref: final && norm(final.label)==='立即下单' ? final.ref : null,
    review_payment: reviewPayment, installment_panel_stable: installmentStable,
    review_full_payment: reviewFullPayment,
    order_pending_payment: pendingReceipt,
    review_target_matches: reviewItems.length>0&&reviewItems.every(x=>x.target_matches),
    review_items: reviewItems,
    review_pickup_deferred: !!deferredPickup,
    review_applecare: !!review && (
      [...review.querySelectorAll("h2,h3")].filter(visible).some(e =>
        /^适用于.*iPhone.*的AppleCare\+服务计划/i.test(norm(e.textContent)))),
    review_company_invoice: /(?:企业|公司).*发票|发票.*(?:企业|公司)|纳税人识别号|税号/.test(reviewText),
    review_subsidy: /(?:国家补贴|政府补贴|优惠券|补贴优惠).*[-−¥￥]|已(?:使用|应用|享受).*(?:补贴|优惠券)/.test(reviewText),
    review_extras_match: extrasMatch,
    review_quantity: reviewPhoneQuantity !== null ? reviewPhoneQuantity :
      reviewPhoneHeadings.length<=1 && reviewPhoneHeadings.every(h=>targetMatches(h.textContent)) && reviewQuantity ? Number(reviewQuantity[1]) : null,
    review_method: reviewItems.length>0&&new Set(reviewItems.map(x=>x.method)).size===1?reviewItems[0].method:'',
    review_store_matches: reviewItems.length>0&&reviewItems.every(x=>x.store_matches),
    // Return dates only, never the review body containing contact information.
    review_pickup_dates: target.pickup_dates && /取货|自提/.test(reviewText) ?
      (reviewText.match(/20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}\s*月\s*\d{1,2}\s*日|今天|明天|后天/g)||[]) : [],
    empty_bag: stage === "BAG" && emptyBagHeading};
  if (stage === "BAG" && !items.length && !out.empty_bag) out.stage = "LOADING";
  // No field values in the fingerprint, only whether saved values were accepted.
  out.fingerprint = JSON.stringify([out.stage, controls.map(c => [c.label,c.selected,c.disabled]),
    fields.map(f => [f.key,f.matches,f.filled,f.invalid,f.editable,f.required]),items,total,errors,out.bag_applecare,out.bag_care_actions,regionOpen,regionStep,regionPicker?{...regionPicker,manual_values:undefined,manual_token:undefined}:null,pickupSchedule,stockIssue,reviewItems,out.review_pickup_deferred,reviewEditRefs,extrasMatch,reviewPayment,reviewFullPayment]);
  if (request.command) {
    if(target.manual_region_token)return {result:'refused',reason:'manual_region_read_only'};
    const c = request.command;
    if (c.kind === 'submit_order') {
      if (c.expected!==out.fingerprint) return {result:'stale'};
      const submit=registry.refs.get(c.ref);
      const verifiedDeferred = c.verified_deferred_pickup===true && out.review_pickup_deferred &&
        target.pickup_date_policy==='earliest' && c.method==='pickup';
      if (stage!=='REVIEW' || c.ref!==out.final_order_ref || !submit?.isConnected || disabled(submit) ||
          norm(label(submit))!=='立即下单' || c.authorized!==true || out.errors ||
          !(out.total>0 && out.total<=c.max_price) || !out.review_target_matches ||
          out.review_quantity!==target.quantity || !out.review_extras_match ||
          out.review_method!==c.method || (c.method==='pickup'&&!out.review_store_matches&&!verifiedDeferred) ||
          !out.review_items.length || !out.review_items.every(r=>r.target_matches && r.method===c.method && (c.method!=='pickup'||r.store_matches||verifiedDeferred)) ||
          out.review_applecare!==(c.applecare==='add') ||
          controls.filter(x=>norm(x.label)==='立即下单'&&!x.disabled).length!==1 ||
          (c.financing==='full' && (!reviewFullPayment || reviewFullPayment!==c.payment)) ||
          (c.financing==='official' && (!reviewPayment || reviewPayment.provider!==c.provider ||
            reviewPayment.months!==c.months || reviewPayment.fee_percent!==0)))
        return {result:'refused',reason:'order_validation'};
      if(registry.orderSubmitted || registry.executed.has(c.nonce)) return {result:'duplicate'};
      registry.orderSubmitted=true;registry.executed.add(c.nonce);
      submit.click();return {result:'dispatched'};
    }
    if (registry.refs.get(c.ref) && forbidden(registry.refs.get(c.ref))) return {result:'refused',reason:'protected_action'};
    if (c.expected !== out.fingerprint) return {result: "stale"};
    const e = registry.refs.get(c.ref);
    // Page redraws may replace a control without changing its label/state.
    // These checks prove no mutation occurred; re-read before trying again.
    if (!e || !e.isConnected) return {result:"stale", reason:"element_replaced"};
    if (disabled(e)) return {result:"stale", reason:"element_disabled"};
    if (e.readOnly && ['set','fill'].includes(c.kind)) return {result:'refused',reason:'readonly_field'};
    if (forbidden(e)) return {result:"refused", reason:"protected_action"};
    if (registry.executed.has(c.nonce)) return {result: "duplicate"};
    registry.executed.add(c.nonce);
    if(c.kind==='set' && /^region_(pickup|delivery)_/.test(c.key || '')) {
      const a=regionActions.get(c.key);
      if(!regionPicker?.configured || !a || a.ref!==c.ref ||
        ![regionPicker.next?.key,regionPicker.recovery?.key].includes(c.key))
        return {result:'refused',reason:'region_changed'};
      if(a.operation==='click') {e.click();return {result:'dispatched'};}
      return safeValue(e,a.value) ? {result:'dispatched'} : {result:'refused',reason:'region_validation'};
    }
    if (c.kind === "click") {
      if (!["button","link","radio"].includes((controls.find(x => x.ref === c.ref) || {}).role)) return {result:"stale", reason:"control_changed"};
      e.click(); return {result: "dispatched"};
    }
    if (c.kind === "remove" && stage === "BAG" &&
        e.matches('button[data-autom="bag-item-remove-button"]')) {
      e.click(); return {result:"dispatched"};
    }
    if (c.kind === "fill" && c.key === "pickup_location" && stage === "FULFILLMENT" && fieldKey(e) === c.key && target.pickup_city)
      return safeValue(e, target.pickup_city) ? {result:"dispatched"} : {result:"refused",reason:"location_validation"};
    if (c.kind === "fill" && c.key !== "pickup_location" && fieldKey(e) === c.key && profile[c.key])
      return safeValue(e, profile[c.key]) ? {result:"dispatched"} : {result:"refused",reason:"field_validation"};
    if (c.kind === "set" && stage === "BAG" && e.matches('select[data-autom="item-quantity-dropdown"]') &&
        Number.isInteger(target.quantity || 1) && (target.quantity || 1)>0 && (target.quantity || 1)<=2 && c.value === String(target.quantity || 1) &&
        items.filter(i=>targetMatches(i.title)&&i.quantity_ref===c.ref).length===1)
      return safeValue(e, c.value) ? {result:"dispatched"} : {result:"refused",reason:"quantity_validation"};
    if (c.kind === "set" && stage === "FULFILLMENT" && choose("我要取货") &&
        out.store_selected && e.tagName==="SELECT" && scheduleKey(e)===c.key &&
        pickupSchedule.some(p=>p.ref===c.ref && !p.disabled && p.choices.filter(o=>o.value===c.value&&!o.disabled).length===1))
      return safeValue(e,c.value) ? {result:"dispatched"} : {result:"refused",reason:"pickup_schedule_validation"};
    return {result: "refused", reason:"unsupported_action"};
  }
  return out;
}
if (typeof module !== "undefined") module.exports = {appleCheckout};

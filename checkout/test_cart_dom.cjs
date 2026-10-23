const {test} = require('node:test'), assert = require('node:assert/strict');
const {JSDOM} = require('jsdom'), fs = require('node:fs'), path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, 'vendor/checkout_browser.js'), 'utf8');
const target = {name: 'iPhone Duo', color: '星光白色', capacity: '1TB', quantity: 1};
function row(care) {
  return `<li data-autom="bag-item-phone"><h2>iPhone Duo 1TB 星光白色</h2>
    <select data-autom="item-quantity-dropdown"><option selected value="1">1</option></select>
    <button data-autom="bag-item-remove-button">移除</button>${care}</li>`;
}
function page(t, care, extra = '') {
  const w = new JSDOM(`<main><h1>购物袋</h1>${row(care)}${extra}
    <p data-autom="total">总计 RMB 9,999</p><button id="checkout">结账</button></main>`,
    {url: 'https://www.apple.com.cn/shop/bag', runScripts: 'outside-only'}).window;
  t.after(() => w.close());
  w.HTMLElement.prototype.getClientRects = function () { return this.hidden ? [] : [{}]; };
  Object.defineProperty(w.document, 'readyState', {value: 'complete'});
  w.performance.getEntriesByType = () => [];
  w.eval(code);
  return w;
}
test('visible target-row coverage offer confirms coverage is not included', t => {
  const w = page(t, '<button>添加 AppleCare+ 服务计划 RMB 1,499</button>');
  const s = w.appleCheckout({target});
  assert.equal(s.stage, 'BAG');
  assert.equal(s.bag_applecare, 'none');
  assert.equal(s.items[0].applecare_included, false);
  assert.equal(s.items[0].quantity, 1);
  assert.equal(s.total, 9999);
});
test('explicit absent coverage differs from missing or hidden coverage details', t => {
  for (const [care, expected] of [
    ['<p>尚未添加 AppleCare+ 服务计划</p>', 'none'],
    ['', 'unconfirmed'],
    ['<button hidden>添加 AppleCare+ RMB 1,499</button>', 'unconfirmed'],
    ['<button disabled>添加 AppleCare+ RMB 1,499</button>', 'unconfirmed'],
    ['<a>了解 AppleCare+ 服务计划</a>', 'unconfirmed'],
    ['<p>AppleCare+ 不包含失窃保障</p>', 'unconfirmed'],
    ['<p>尚未添加 AppleCare+ 时，可以稍后购买</p>', 'unconfirmed'],
  ]) {
    const w = page(t, care), s = w.appleCheckout({target});
    assert.equal(s.bag_applecare, expected, care);
    assert.equal(s.items[0].applecare_included, expected === 'none' ? false : null, care);
  }
});
test('included coverage takes precedence over an upsell in the same row', t => {
  const w = page(t, '<p>已添加 AppleCare+ 服务计划</p><button>添加 AppleCare+ RMB 1,499</button>');
  const s = w.appleCheckout({target});
  assert.equal(s.bag_applecare, 'included');
  assert.equal(s.items[0].applecare_included, true);
});
test('coverage evidence outside the phone row does not authorize cart reuse', t => {
  const w = page(t, '', '<aside><button>添加 AppleCare+ RMB 1,499</button><p>未添加 AppleCare+</p></aside>');
  assert.equal(w.appleCheckout({target}).bag_applecare, 'unconfirmed');
});
test('each split phone row must confirm absent coverage', t => {
  const w = page(t, '<p>未添加 AppleCare+</p>', row(''));
  const s = w.appleCheckout({target});
  assert.equal(s.bag_applecare, 'unconfirmed');
  assert.equal(s.items[0].applecare_included, false);
  assert.equal(s.items[1].applecare_included, null);
});
test('coverage changes invalidate a previously read checkout action', t => {
  const w = page(t, '<p id="care">未添加 AppleCare+</p>');
  const s = w.appleCheckout({target});
  let clicks = 0;
  w.document.querySelector('#checkout').onclick = () => clicks++;
  w.document.querySelector('#care').textContent = '已添加 AppleCare+';
  const result = w.appleCheckout({target, command: {
    kind: 'click', ref: s.controls.find(c => c.label === '结账').ref,
    expected: s.fingerprint, nonce: 'offline-checkout-test',
  }});
  assert.equal(result.result, 'stale');
  assert.equal(clicks, 0);
});

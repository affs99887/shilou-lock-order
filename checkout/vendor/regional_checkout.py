"""Region policy and trusted monitor-catalog mapping, with no network access.

Foreign adapters are experimental until live acceptance. A foreign order can
authorise a card charge: no international adapter is allowed to submit orders.
"""
import copy
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent

def asset(name):
    for path in (ROOT / name, ROOT.parent.parent / 'src/lib' / name):
        if path.is_file():
            return path
    raise ValueError('地区适配资源缺失；未执行选购')

REGIONS = {r['locale']: r for r in json.loads(asset('checkout-regions.json').read_text(encoding='utf-8'))}

def region(locale):
    if locale not in REGIONS:
        raise ValueError('该地区不在当前监控支持范围')
    return REGIONS[locale]

def allowed_url(url, locale, navigation=False):
    """Only selected Apple storefront; never wildcard arbitrary apple subdomains."""
    p, base = urlsplit(url), urlsplit(region(locale)['base'])
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
        return False
    allowed = r'(?:www|secure[0-9]+\.www|secure\.store|store)\.apple\.com' + (r'\.cn' if locale == 'zh_CN' else '')
    if not re.fullmatch(allowed, p.hostname or ''):
        return False
    prefix = base.path + '/shop/'
    if not p.path.startswith(prefix) or '%' in p.path or '/..' in p.path or '\\' in p.path:
        return False
    if navigation:
        return p.hostname == base.hostname and not p.query and not p.fragment
    return True

def catalog():
    return json.loads(asset('checkout-catalog.json').read_text(encoding='utf-8'))

def international_plan(plan, hit):
    locale = hit['target'].get('locale')
    r = region(locale)
    if locale == 'zh_CN':
        raise ValueError('中国大陆应使用原选购适配器')
    if plan.get('checkout_locale', 'zh_CN') != locale:
        raise ValueError('库存地区与选购资料、预算地区不一致；未操作购物袋')
    if plan.get('auto_submit_order'):
        raise ValueError('国际选购不自动提交订单，避免触发扣款或授信')
    if plan.get('financing') != 'manual' or plan.get('payment') != '官网手动选择':
        raise ValueError('国际付款及分期须在当地官网本人选择；不能套用大陆渠道')
    c = catalog().get(locale, {})
    t, product = hit['target'], hit['product']
    item = c.get('products', {}).get(t.get('partNumber'))
    store = c.get('stores', {}).get(t.get('storeNumber'))
    if not item or not store or product.get('partNumber') != t.get('partNumber'):
        raise ValueError('配置或门店未匹配本地区官方目录；未操作购物袋')
    if product.get('category') != 'iphone' or item['category'] != 'iphone':
        raise ValueError('自动选购目前仅适配 iPhone；其他品类继续监控')
    # Monitor wire uses familyType (iphone18promax), not the buying-page slug
    # (iphone-18-pro). In particular Pro and Pro Max share a purchase page.
    if product.get('family') != item['dimensions'].get('familyType', item['family']):
        raise ValueError('监控机型与本地区目录不符；未操作购物袋')
    # No cn colour-name validation here. Exact local SKU and local dimensions
    # come from the same bundled official catalog used by apw-core.
    from checkout_engine import resolve_pickup_preferences
    p = resolve_pickup_preferences(plan, item['dimensions'].get('familyType', item['family']))
    p.update(checkout_locale=locale, currency=r['currency'], fulfillment='pickup',
             part_number=t['partNumber'], regional_product=item, regional_store=store,
             product_url=r['base'] + '/shop/buy-iphone/' + item['family'],
             bag_url=r['base'] + '/shop/bag', store_id=t['storeNumber'],
             store=store['name'], bark_enabled=False, auto_submit_order=False)
    return p

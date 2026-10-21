"""Package a separate, unnotarized Mac candidate, never replace the stable app."""
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
import zipfile

root=Path(__file__).resolve().parents[1]
delivery=root.parents[1]/'交付'
source=root/'target/release/bundle/macos/拾漏锁单·国际选购验证.app'
version=json.loads((root/'src-tauri/tauri.international.conf.json').read_text())['version']
assert version.startswith('0.6.2-intl.') and version.removeprefix('0.6.2-intl.').isdigit()
stage_name='拾漏锁单-国际选购验证'+('' if version=='0.6.2-intl.1' else '-'+version)
stage=delivery/'.国际选购验证暂存.noindex'/stage_name
archive=delivery/('拾漏锁单-国际选购验证-Mac-'+version+'.zip')
refresh='--refresh-helper' in sys.argv[1:]
repair='--repair-zip' in sys.argv[1:] or refresh
if repair:
    source=stage/source.name
with (source/'Contents/Info.plist').open('rb') as file:
    info=plistlib.load(file)
assert info['CFBundleIdentifier']=='local.apple-pickup-watcher.auto.international'
assert info['CFBundleShortVersionString']==version
helper=source/'Contents/Resources/resources/apw-checkout-international'
if refresh:
    shutil.copy2(root/'src-tauri/resources/apw-checkout-international',helper)
assert helper.read_bytes()==(root/'src-tauri/resources/apw-checkout-international').read_bytes()
if not repair and (stage.exists() or archive.exists()):
    raise RuntimeError('Delivery already exists; do not overwrite a previous candidate')
stage.mkdir(parents=True,exist_ok=repair)
app=stage/source.name
if not repair:
    shutil.move(source,app)
else:
    old=stage.parent/('previous-archive-'+str(time.time_ns())+'.zip')
    archive.replace(old)
shutil.copytree(root/'src-tauri/resources/OpenSourceInternational',app/'Contents/Resources/resources/OpenSourceInternational',dirs_exist_ok=True)
(stage/'使用说明.txt').write_text(f'''拾漏锁单 · 国际选购验证 {version}

Mac M 系列，macOS 13 或更新系统。解压后打开软件，或拖到“应用程序”。
这是独立、本地 ad-hoc 签名、未经过 Apple 公证的验证包，不覆盖当前拾漏锁单。
不要为了运行软件关闭系统安全保护。系统如阻止打开，先核对来源后按系统提示处理。

七地区：大陆、香港、台湾、日本、新加坡、澳大利亚、马来西亚。
原库存监控保留；自动选购本轮只做 iPhone。海外适配尚未完成真实官网全流程验收，不是全地区正式可售版。

使用顺序：
1. 捡漏中心添加当地地区的机型、容量、颜色、门店。
2. 选购方案选择同一地区，填写当地货币总预算。切地区会清空旧币种预算。
3. 填写并保存本人的当地资料；各地区独立加密，不复制银行卡、密码或验证码。
4. 先测试演练。需要登录、验证码、地址手选、付款或分期时，软件显示醒目提示，同时通过已配置的 Bark 提醒。
   同一页面同一步骤不反复推送；登录验证完成后自动重新识别，其余步骤按提示处理后点“人工处理后继续”。
   进入付款、银行卡验证、授信、签约及最终下单／支付均由本人完成。Bark 未收到时请检查手机提醒设置。
5. 再开启监测并启用有货自动选购；只推进一套订单，不与旧版同时监控。

海外下单可能直接申请银行卡授权。因此国际流程不自动提交订单、不支付、不签约，也不承诺“锁单待支付”。
国际 AppleCare、不能明确核验的日期／地址／新页面会交给本人处理，不擅自改变设置。
国内付款及分期仍走原适配器。任何演练都不提交订单。

资料和监控设置使用独立目录，启动默认停止。本次没有替换稳定版。
地区格式检查、本地模拟选购和界面测试已做；真实六地区选配、加购、取货、付款及缺货切店仍未验收。
公开商品入口可读不代表已经通过抢购测试。没有创建订单或付款，不保证抢到。

首次需要允许软件控制 Safari，以及 Safari 开发菜单中的“允许来自 Apple 事件的 JavaScript”。
登录和验证只在 Apple 官网本人完成。Bark 链接在手机提醒页自行填写。

独立工具，非 Apple 官方。开源许可证及对应源码在应用包 Contents/Resources/resources/OpenSourceInternational 内。
''',encoding='utf-8')
subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as package:
    for path in sorted(stage.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('Unexpected application symlink; do not silently follow it')
        package.write(path,path.relative_to(stage.parent))
with zipfile.ZipFile(archive) as package:
    assert package.testzip() is None
    prefix=stage.name+'/'
    top={name[len(prefix):].split('/')[0] for name in package.namelist() if name.startswith(prefix) and len(name)>len(prefix)}
    assert top=={app.name,'使用说明.txt'},top
print({'archive':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
       'zip_top_level_items':sorted(top),'stable_app_replaced':False,'native_app_started':False})

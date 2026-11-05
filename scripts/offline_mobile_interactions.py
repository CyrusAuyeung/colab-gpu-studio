"""Focused synthetic mobile UI checks. No task submission or real provider calls."""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE=os.environ.get('OFFLINE_BROWSER_BASE','http://127.0.0.1:18793')
assert BASE.startswith('http://127.0.0.1:')
OUT=Path('docs/offline-evidence')
report={'fixture':True,'physical_android_keyboard':False,'errors':[],'external':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('OFFLINE_CHROMIUM','/opt/data/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome'),args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True)
    def route(r):
        if r.request.url.startswith(BASE+'/'):
            r.continue_()
        else:
            report['external'].append(r.request.url)
            r.abort()
    context.route('**/*',route)
    page=context.new_page()
    page.on('pageerror',lambda e:report['errors'].append(str(e)))
    page.goto(BASE)
    page.locator('input[type=password]').fill('mock-local-only')
    page.get_by_role('button',name='进入工作台').click()
    page.get_by_role('button',name='立即生成').wait_for()
    # Upload a harmless user reference; this is isolated temporary storage.
    result=page.evaluate('''async()=>{const r=await fetch('/api/projects');return (await r.json())}''')
    report['projects_read']=bool(result)
    page.set_viewport_size({'width':390,'height':430})
    field=page.locator('textarea,input[type=text]').first
    field.fill('手机输入测试 — '+('长文案 '*30))
    field.focus()
    field.scroll_into_view_if_needed()
    report['focused_field_in_view']=field.evaluate('e=>{const r=e.getBoundingClientRect();return r.bottom>0&&r.top<innerHeight}')
    report['keyboard_horizontal_overflow']=page.evaluate('document.documentElement.scrollWidth>innerWidth')
    page.screenshot(path=str(OUT/'screenshots/mobile-input-short.png'))
    assert report['focused_field_in_view'] and not report['keyboard_horizontal_overflow']
    page.set_viewport_size({'width':390,'height':844})
    page.get_by_role('navigation').locator('button').filter(has_text='作品').click()
    page.get_by_label('搜索作品',exact=True).fill('不存在的作品')
    assert page.get_by_text('暂无生成作品',exact=True).is_visible()
    report['empty_gallery']=True
    # Explicit synthetic gallery response, media bytes served by local-only route.
    import base64
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
    page.route('**/api/gpu/gallery*',lambda r:r.fulfill(json={'data':[{'id':'mobile-fixture','filename':'Mock 手机预览.png','mime':'image/png','mock':True,'url':BASE+'/fixture.png','thumbnail_url':BASE+'/fixture.png'}]}))
    page.route('**/fixture.png',lambda r:r.fulfill(body=png,content_type='image/png'))
    page.reload()
    page.get_by_role('navigation').locator('button').filter(has_text='作品').click()
    page.locator('article button').first.click()
    dialog=page.get_by_role('dialog',name='查看作品')
    dialog.get_by_role('button',name='全屏查看',exact=True).click()
    page.wait_for_function('Boolean(document.fullscreenElement)')
    report['fullscreen_opened']=True
    page.screenshot(path=str(OUT/'screenshots/mobile-viewer-fullscreen.png'))
    page.evaluate('document.exitFullscreen()')
    dialog.get_by_role('button',name='全屏查看',exact=True).click()
    page.wait_for_function('Boolean(document.fullscreenElement)')
    dialog.get_by_role('button',name='关闭',exact=True).click()
    page.wait_for_function('!document.fullscreenElement')
    assert dialog.count()==0
    report['viewer_closed']=True
    assert not report['errors'] and not report['external']
    browser.close()
(OUT/'mobile-interactions.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))

"""Explicit synthetic list UI fixtures; no production data or paid actions."""
import os
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE=os.environ.get('OFFLINE_BROWSER_BASE','http://127.0.0.1:18789')
assert BASE.startswith('http://127.0.0.1:'), 'local-only test origin required'
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('OFFLINE_CHROMIUM','/opt/data/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome'),headless=True,args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True)
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    external=[]
    def route(r):
        if not r.request.url.startswith(BASE+'/'):
            external.append(r.request.url)
            r.abort()
        elif r.request.method not in {'GET','HEAD'} and not r.request.url.endswith('/api/auth/login'):
            # Login endpoint is checked separately by path below if needed.
            r.continue_() if '/login' in r.request.url else r.abort()
        else:
            r.continue_()
    context.route('**/*',route)
    page.goto(BASE)
    page.locator('input[type=password]').fill('mock-local-only')
    page.get_by_role('button',name='进入工作台').click()
    page.get_by_role('button',name='立即生成').wait_for()
    # List fixtures use the requested project identity; no credential extraction.
    jobs=[{'id':f'fixture-job-{i:04d}','workflow_key':f'MOCK long workflow {i}', 'provider':'mock','state':'completed','attempt_number':1} for i in range(200)]
    png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    gallery=[{'id':f'fixture-asset-{i:04d}','filename':f'MOCK-{i:04d}.png','mime':'image/png','thumbnail_url':png,'download_url':png,'mock':True} for i in range(1000)]
    def fixture_jobs(r):
        from urllib.parse import parse_qs,urlparse
        project_id=parse_qs(urlparse(r.request.url).query)['project_id'][0]
        r.fulfill(json={'data':[{**job,'project_id':project_id} for job in jobs]})
    page.route('**/api/gpu/jobs?*',fixture_jobs)
    page.route('**/api/gpu/gallery?*',lambda r:r.fulfill(json={'data':gallery}))
    page.reload()
    page.get_by_role('button',name='立即生成').wait_for()
    start=time.perf_counter()
    page.get_by_role('navigation').locator('button').filter(has_text='任务').click()
    page.get_by_text('MOCK long workflow 0',exact=True).wait_for()
    initial_jobs=page.locator('article').count()
    assert initial_jobs==30,initial_jobs
    page.get_by_role('button',name='加载更多任务',exact=True).click()
    assert page.locator('article').count()==60
    page.get_by_label('搜索任务',exact=True).fill('fixture-job-0199')
    assert page.locator('article').count()==1
    page.get_by_role('navigation').locator('button').filter(has_text='作品').click()
    page.get_by_text('1000 件作品',exact=True).wait_for()
    initial_assets=page.locator('article').count()
    assert initial_assets==24,initial_assets
    page.get_by_role('button',name='加载更多作品',exact=True).click()
    assert page.locator('article').count()==48
    page.get_by_label('搜索作品',exact=True).fill('MOCK-0999')
    assert page.locator('article').count()==1
    overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth')
    assert not overflow and not errors and not external
    report={'synthetic':True,'input_jobs':200,'input_assets':1000,'initial_job_nodes':initial_jobs,'initial_asset_nodes':initial_assets,'load_more_and_search':True,'overflow':overflow,'page_errors':errors,'external_requests':external,'interaction_seconds':round(time.perf_counter()-start,3)}
    Path('docs/offline-evidence/long-list-browser-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
    browser.close()

"""Browser retry acceptance against the isolated fail-first-start server."""
import os
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE=os.environ.get('OFFLINE_BROWSER_BASE','http://127.0.0.1:18789')
assert BASE.startswith('http://127.0.0.1:'), 'local-only test origin required'
assert httpx.get(BASE+'/api/health',trust_env=False).status_code==200
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('OFFLINE_CHROMIUM','/opt/data/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome'),headless=True,args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True)
    external=[]
    def route(request):
        if request.request.url.startswith(BASE+'/'):
            request.continue_()
        else:
            external.append(request.request.url)
            request.abort()
    context.route('**/*',route)
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(BASE)
    page.locator('input[type=password]').fill('mock-local-only')
    page.get_by_role('button',name='进入工作台').click()
    rejected=[]
    def reject_once(route):
        rejected.append(route.request.headers.get('idempotency-key'))
        if len(rejected)>1:
            route.continue_()
            return
        route.fulfill(status=503,content_type='application/json',body=json.dumps({'error':{'code':'SCHEDULER_BUSY','message':'模拟调度忙，请重试'}}))
    page.route('**/api/gpu/batches',reject_once)
    page.get_by_role('button',name='立即生成').click()
    dialog=page.get_by_role('dialog',name='确认提交任务')
    dialog.get_by_role('button',name='确认启动').click()
    dialog.get_by_role('alert').filter(has_text='模拟调度忙').wait_for()
    assert dialog.is_visible()
    assert dialog.get_by_role('button',name='确认启动').is_enabled()
    with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith(('/api/gpu/jobs','/api/gpu/batches'))) as posted:
        page.get_by_role('dialog',name='确认提交任务').get_by_role('button',name='确认启动').click()
    assert posted.value.status==202,posted.value.text()
    assert posted.value.request.headers.get('idempotency-key')==rejected[0]
    job=posted.value.json()['data']
    if posted.value.url.endswith('/batches'):
        rows=context.request.get(BASE+'/api/gpu/jobs').json()['data']
        job=next(row for row in rows if row.get('batch_id')==job['id'])
    job_id=job['id']
    def wait_state(target):
        for _ in range(150):
            current=context.request.get(BASE+'/api/gpu/jobs/'+job_id).json()['data']
            if current['state']==target:
                return current
            page.wait_for_timeout(100)
        raise AssertionError(current)
    failed=wait_state('failed')
    page.get_by_role('navigation').locator('button').filter(has_text='任务').click()
    page.get_by_label('搜索任务',exact=True).fill(job_id)
    page.get_by_role('button',name='刷新',exact=True).click()
    with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/attempts')) as retried:
        page.get_by_role('button',name='重试本任务',exact=True).click()
    assert retried.value.status==202,retried.value.text()
    assert retried.value.json()['data']['id']==job_id
    complete=wait_state('completed')
    attempts=context.request.get(BASE+'/api/gpu/jobs/'+job_id+'/attempts').json()['data']
    assert [a['state'] for a in attempts]==['failed','completed'],attempts
    assert complete['attempt_number']==2
    assert len(complete['output_asset_ids'])==1
    assets=context.request.get(BASE+'/api/gpu/jobs/'+job_id+'/outputs').json()['data']
    assert len(assets)==1
    assert complete['runtime_deleted']
    page.get_by_role('button',name='刷新',exact=True).click()
    page.get_by_text('执行次数：2',exact=True).wait_for()
    page.get_by_role('button',name='查看详情',exact=True).click()
    detail=page.get_by_role('dialog',name='任务详情')
    detail.get_by_text('第 1 次',exact=False).wait_for()
    detail.get_by_text('第 2 次',exact=False).wait_for()
    assert '失败' in detail.inner_text() and '完成' in detail.inner_text()
    page.screenshot(path='docs/offline-evidence/screenshots/retry-attempt-390.png')
    assert not external and not errors,(external,errors)
    report={'rejected_submit_keeps_dialog_and_key':True,'job_id_unchanged':True,'attempt_states':[a['state'] for a in attempts],'attempt_number':2,'verified_outputs':len(assets),'runtime_deleted':True,'page_errors':errors,'external_requests':external}
    Path('docs/offline-evidence/attempt-browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))
    browser.close()

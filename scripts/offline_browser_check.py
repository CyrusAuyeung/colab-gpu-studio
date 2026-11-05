"""Real local browser + real local API/worker, with external browser traffic denied."""

import os
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("OFFLINE_BROWSER_BASE", "http://127.0.0.1:18788")
OUT = Path("docs/offline-evidence")
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "screenshots").mkdir(exist_ok=True)
report = {"page_errors": [], "external_requests": [], "screens": [], "batch_requests": 0}
assert BASE.startswith('http://127.0.0.1:'), 'local-only test origin required'
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ.get("OFFLINE_CHROMIUM", "/opt/data/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"),
        headless=True,
        args=["--no-sandbox"],
    )
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)

    def intercept(route):
        if route.request.url.startswith((BASE + "/", "data:", "blob:")):
            route.continue_()
        else:
            report["external_requests"].append(route.request.url)
            route.abort()

    context.route("**/*", intercept)
    page = context.new_page()
    page.on("pageerror", lambda err: report["page_errors"].append(str(err)))

    def track(request):
        if request.method == "POST" and request.url.endswith("/api/gpu/batches"):
            report["batch_requests"] += 1

    page.on("request", track)
    page.goto(BASE)
    page.locator("input[type=password]").fill("mock-local-only")
    page.get_by_role("button", name="进入工作台").click()
    page.get_by_role("button", name="立即生成").wait_for()
    existing=context.request.get(BASE+'/api/gpu/jobs').json()['data']
    if isinstance(existing,dict): existing=existing.get('items',existing.get('jobs',[]))
    baseline_ids={job['id'] for job in existing}
    page.get_by_label("生成数量", exact=True).fill("3")
    page.get_by_label("运行策略", exact=True).select_option("batch")
    page.get_by_role("button", name="立即生成").click()
    with page.expect_response(lambda response: response.request.method=='POST' and response.url.endswith('/api/gpu/batches')) as submitted:
        page.get_by_role("dialog", name="确认提交任务").get_by_role("button", name="确认启动").evaluate("button=>{button.click();button.click()}")
    assert submitted.value.status==202,submitted.value.text()
    page.wait_for_timeout(1800)
    report["after_submit"] = page.locator("body").inner_text()
    assert report["batch_requests"] == 1, report
    # Poll the actual local API, not DOM text or a canned response.
    job_rows=[]
    for _ in range(80):
        response=context.request.get(BASE+'/api/gpu/jobs')
        assert response.ok,response.text()
        job_rows=response.json()['data']
        if isinstance(job_rows,dict): job_rows=job_rows.get('items',job_rows.get('jobs',[]))
        job_rows=[job for job in job_rows if job['id'] not in baseline_ids]
        if len(job_rows)==3 and all(job['state']=='completed' for job in job_rows): break
        page.wait_for_timeout(150)
    assert len(job_rows)==3 and all(job['state']=='completed' for job in job_rows),job_rows
    report['completed_mock_jobs']=len(job_rows)
    gallery=context.request.get(BASE+'/api/gpu/gallery').json()['data']
    if isinstance(gallery,dict): gallery=gallery.get('items',gallery.get('assets',[]))
    assert len(gallery)>=3,gallery
    report['mock_gallery_assets']=len(gallery)

    for theme in ["dark", "light"]:
        page.evaluate(
            '(theme)=>{document.documentElement.dataset.theme=theme;localStorage.setItem("studio-theme",theme)}',
            theme,
        )
        for width in [360, 390, 412, 430, 768, 1024, 1440]:
            page.set_viewport_size({"width": width, "height": 844})
            for name in ["创作", "任务", "作品", "算力", "设置"]:
                if name == "设置":
                    page.get_by_role("button", name="打开设置", exact=True).click()
                else:
                    page.get_by_role("navigation").locator("button").filter(has_text=name).click()
                page.evaluate("scrollTo(0,0)")
                page.wait_for_timeout(100)
                metrics = page.evaluate(
                    "({width:innerWidth,scroll:document.documentElement.scrollWidth,height:innerHeight})"
                )
                report["screens"].append({"page": name, "theme": theme, **metrics})
                if width == 390:
                    page.screenshot(path=str(OUT / "screenshots" / f"{name}-390-{theme}.png"), full_page=False)
    page.get_by_role('navigation').locator('button').filter(has_text='作品').click()
    page.get_by_label('搜索作品',exact=True).fill('no-such-asset-unique-marker')
    assert page.locator('img').count()==0
    page.get_by_label('搜索作品',exact=True).fill('')
    for image in page.locator('img').all():
        image.scroll_into_view_if_needed()
        image.evaluate("img=>img.loading='eager'")
        image.evaluate("img=>img.decode()")
    page.wait_for_function("Array.from(document.images).every(img=>img.complete&&img.naturalWidth>0)")
    report['gallery_images_loaded']=page.locator('img').count()
    assert report['gallery_images_loaded']>=3
    # Simulated keyboard viewport; this does not claim physical Android keyboard QA.
    page.set_viewport_size({'width':390,'height':430})
    page.get_by_role('navigation').locator('button').filter(has_text='创作').click()
    report['keyboard_viewport_overflow']=page.evaluate('document.documentElement.scrollWidth>innerWidth')
    assert not report['keyboard_viewport_overflow']
    page.reload()
    page.get_by_role("button", name="打开设置", exact=True).wait_for()
    report["session_survived_reload"] = True
    browser.close()
(OUT / "browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
overflow = [r for r in report["screens"] if r["scroll"] > r["width"]]
print(
    json.dumps(
        {
            "batch_requests": report["batch_requests"],
            "screens": len(report["screens"]),
            "overflow": overflow,
            "page_errors": report["page_errors"],
            "external_requests": report["external_requests"],
        },
        ensure_ascii=False,
    )
)
assert not overflow
assert not report["page_errors"]
assert not report["external_requests"]

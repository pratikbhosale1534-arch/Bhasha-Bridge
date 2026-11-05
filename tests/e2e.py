"""Browser E2E (Playwright + local headless Chromium). Run: python tests/e2e.py <demo|mock> """
import os, subprocess, sys, time, signal, tempfile, urllib.request
from playwright.sync_api import sync_playwright, expect

mode = sys.argv[1]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = "/mnt/user-data/outputs/screens"; os.makedirs(OUT, exist_ok=True)
env = dict(os.environ, APP_DB=os.path.join(tempfile.mkdtemp(), "e2e.db"), PYTHONWARNINGS="ignore")
for k in ("WATSONX_API_KEY", "WATSONX_PROJECT_ID", "DEMO_MODE"): env.pop(k, None)
procs = []
if mode == "mock":
    procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "tests.fake_watsonx:app", "--port", "9100", "--log-level", "error"], cwd=ROOT))
    env.update(WATSONX_API_KEY="fake", WATSONX_PROJECT_ID="fake", WATSONX_URL="http://127.0.0.1:9100", IBM_IAM_URL="http://127.0.0.1:9100/identity/token")
procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", "9101", "--log-level", "error"], cwd=ROOT, env=env))
time.sleep(4)
errors = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell")
        pg = b.new_page(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "favicon" not in m.text and "ERR_NAME_NOT_RESOLVED" not in m.text else None)
        pg.goto("http://127.0.0.1:9101/")
        banner = pg.locator("#trAiBanner")
        if mode == "demo": expect(banner).to_contain_text("DEMO MODE")
        else: expect(banner).to_contain_text("Using IBM Granite (ibm/granite-4-h-small)")
        pg.get_by_role("button", name="Physics: Newton's Laws (TXT)").click()
        expect(pg.locator("#trDocInfo")).to_contain_text("newton_laws.txt")
        expect(pg.locator(".tr-sec")).to_have_count(3)
        pg.select_option("#trTgt", "mr"); expect(pg.locator("#trLangNote")).to_contain_text("not on IBM's official")
        pg.click("#trRunT")
        out0 = pg.locator("#trOut0 .tr-text")
        expect(out0).to_contain_text("DEMO MODE" if mode == "demo" else "[MOCK-अनुवाद]", timeout=15000)
        if mode == "mock":
            expect(pg.locator("#trOut2 .tr-text")).to_contain_text("def force(mass, acceleration)")  # code preserved
        expect(pg.locator("#trHist .tr-hist-item")).to_have_count(1)
        pg.locator("#trWork").screenshot(path=f"{OUT}/{mode}_desktop_workspace.png")
        if mode == "mock":
            pg.click("#trRunG"); expect(pg.locator("#trGloss table")).to_contain_text("जड़त्व", timeout=10000)
            pg.click("#trRunS"); pg.locator("#trOut0 .badge").first.wait_for()
            expect(pg.locator("#trOut0 .badge").first).to_contain_text("not a translation", timeout=10000)
            pg.select_option("#trDlWhat", "translation")
            for fmt in ("docx", "md"):
                pg.select_option("#trDlFmt", fmt)
                with pg.expect_download() as d: pg.click("#trDl")
                print("downloaded", d.value.suggested_filename)
            pg.locator("#trHist .tr-hist-item button", has_text="Open").first.click()
            expect(pg.locator("#trHistView")).to_contain_text("not stored")
        # error handling: unsupported file
        pg.set_input_files("#trFile", {"name": "bad.exe", "mimeType": "application/octet-stream", "buffer": b"MZ"})
        expect(pg.locator("#trError")).to_contain_text("Unsupported file type")
        # mobile viewport
        pg.set_viewport_size({"width": 390, "height": 844}); pg.reload()
        pg.get_by_role("button", name="Biology: Photosynthesis (DOCX)").click()
        expect(pg.locator(".tr-sec").first).to_be_visible()
        sw = pg.evaluate("document.documentElement.scrollWidth"); print("mobile scrollWidth", sw)
        assert sw <= 392, f"horizontal overflow on mobile: {sw}"
        pg.locator("#trWork").scroll_into_view_if_needed()
        pg.locator("#trWork").screenshot(path=f"{OUT}/{mode}_mobile.png")
        b.close()
    print(f"E2E [{mode}] PASSED; page errors: {errors or 'none'}")
finally:
    for pr in procs: pr.send_signal(signal.SIGTERM)
sys.exit(1 if errors else 0)

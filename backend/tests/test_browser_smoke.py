"""Browser smoke tests of key user flows (Playwright + headless Chromium).

Skipped unless the frontend is built (frontend/dist) and Playwright with a
Chromium build is installed. Run: pytest -m browser
"""

import os
import socket
import threading
import time
from pathlib import Path

import pytest
from conftest import FIXTURES, small_spec

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
pw = pytest.importorskip("playwright.sync_api")
pytestmark = [pytest.mark.browser, pytest.mark.skipif(not (DIST / "index.html").exists(), reason="frontend not built (cd frontend && node build.mjs)")]

IGNORED_CONSOLE = ("ERR_TUNNEL_CONNECTION_FAILED", "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED", "Failed to load resource", "fonts.g")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn

    from ember_atlas.api.app import create_app
    from ember_atlas.service import EmberService
    from ember_atlas.store.sqlite_store import SqliteStore
    from ember_atlas.synthetic.seed import seed_demo

    st = SqliteStore(tmp_path_factory.mktemp("browser") / "b.sqlite")
    st.migrate()
    seed_demo(st, spec=small_spec())
    app = create_app(EmberService(st), frontend_dist=DIST)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # no browser build installed
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture()
def page(browser, server):
    pg = browser.new_page(viewport={"width": 1400, "height": 1000}, accept_downloads=True)
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and not any(s in m.text for s in IGNORED_CONSOLE) and pg.errors.append(m.text))
    pg.goto(server)
    pg.wait_for_selector("rect.cell", timeout=60000)
    yield pg
    assert pg.errors == [], pg.errors
    pg.close()


def test_loads_with_synthetic_labelling_and_calendar(page):
    assert "not NASA measurements" in page.inner_text(".synthetic-band")
    assert page.locator("rect.cell").count() > 300  # 7 years x 52 weeks
    assert page.locator(".legend").first.inner_text().count("coverage unknown") == 1


def test_selecting_a_week_links_map_and_details(page):
    page.locator("rect.cell").nth(52 * 4 + 33).click()
    page.wait_for_selector("section[aria-label='Selected period'] table.data", timeout=60000)
    rows = page.locator("section[aria-label='Selected period'] table.data tbody tr")
    assert rows.count() >= 2
    assert page.inner_text("section[aria-label='Map'] h3").startswith("Map: Week of")
    page.wait_for_function("document.querySelectorAll('.map-wrap circle, .map-wrap rect').length > 0", timeout=60000)


def test_keyboard_navigation_moves_selection(page):
    cal = page.locator(".calendar svg")
    cal.focus()
    page.keyboard.press("Home")
    page.keyboard.press("ArrowRight")
    assert page.locator("rect.cell.sel").count() == 1


def test_tabs_render(page):
    page.click("#tabbtn-compare")
    page.wait_for_selector(".status-chip", timeout=120000)
    assert "What you get by splicing raw counts" in page.inner_text("#tab-compare")
    page.click("#tabbtn-validation")
    assert "Blocked hold-out validation" in page.inner_text("#tab-validation")
    page.click("#tabbtn-method")
    assert "What the numbers are" in page.inner_text("#tab-method")
    page.click("#tabbtn-anomalies")
    page.wait_for_selector("text=Critical periods", timeout=120000)


def test_invalid_area_shows_explicit_error(page):
    page.fill("input[aria-label='Bounding box: west, south, east, north']", "10, 5, 9")
    page.click("text=Use box")
    assert "four numbers" in page.inner_text("[role=alert]")
    page.fill("input[aria-label='Bounding box: west, south, east, north']", "10, 5, 11, 4")
    page.click("text=Use box")
    page.wait_for_selector("text=south must be less than north", timeout=10000)


def test_export_downloads_labelled_file(page):
    with page.expect_download(timeout=60000) as d:
        page.select_option("select[aria-label='Export']", "series|csv")
    assert d.value.suggested_filename.startswith("SYNTHETIC_ember_series_")
    path = d.value.path()
    text = Path(path).read_text()
    assert "SYNTHETIC DEMO DATA" in text.splitlines()[0]


def test_import_flow_reports_rows(page):
    page.click("#tabbtn-import")
    page.set_input_files("#csv", str(FIXTURES / "SYNTHETIC_tiny_modis_sp.csv"))
    page.click("text=Synthetic test data")
    page.click("text=Import file")
    page.wait_for_selector("text=Rows read", timeout=30000)
    txt = page.inner_text("#tab-import")
    assert "Duplicates" in txt and "SYNTHETIC_tiny_modis_sp.csv" in txt
    # the key field is a password input and is not persisted anywhere
    assert page.get_attribute("#mapkey", "type") == "password"
    assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0


@pytest.mark.skipif(os.environ.get("EMBER_SMOKE_MOBILE") == "0", reason="disabled")
def test_mobile_layout_has_no_horizontal_scroll(browser, server):
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.goto(server)
    pg.wait_for_selector("rect.cell", timeout=60000)
    pg.wait_for_timeout(1500)
    offenders = pg.evaluate(
        """() => { const W = window.innerWidth; const out = [];
        for (const el of document.querySelectorAll('body *')) {
          if (el.closest('svg') && el.tagName.toLowerCase() !== 'svg') continue;  // clipped inside the map/chart
          const r = el.getBoundingClientRect();
          if (r.right > W + 1 && r.width > 0) out.push(el.tagName + '.' + el.className + ' ' + Math.round(r.right)); }
        return out.slice(0, 10); }"""
    )
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), offenders
    pg.close()

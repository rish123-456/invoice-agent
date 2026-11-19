"""Auto-pilot for recording the demo video.

Opens a visible browser and drives the app through the demo in order.
It waits for you to press ENTER in the terminal before each step, so you
control the pace while you narrate. Start your screen recorder (Loom),
record the browser window, and keep this terminal off-screen.

Setup (once):
    pip install playwright
    playwright install chromium

Run:
    python autopilot.py                                  # local app at localhost:8501
    python autopilot.py https://your-app.streamlit.app   # deployed app
    python autopilot.py URL --auto 25                    # no ENTER, 25 s per step
"""
import sys

from playwright.sync_api import sync_playwright

URL = next((a for a in sys.argv[1:] if a.startswith("http")), "http://localhost:8501")
AUTO = float(sys.argv[sys.argv.index("--auto") + 1]) if "--auto" in sys.argv else None
HEADLESS = "--headless" in sys.argv  # only for testing the script

STEPS = [
    ("01_happy_bharat_furniture", "Happy path - clean invoice, exact PO match"),
    ("05_edge_duplicate_bharat", "Edge 1 - disguised duplicate of the invoice we just approved"),
    ("03_split_po_nimbus_milestone1", "Split PO - milestone 1 of 3"),
    ("04_edge_split_po_overbill_nimbus", "Edge 2 - milestones 2+3 over-bill the remaining PO balance"),
    ("02_edge_scanned_no_po_shakti", "Edge 3 - scanned image, no PO number"),
    ("06_edge_price_variance_gst_inclusive", "Edge 4 - GST-inclusive prices + price variance"),
]


def wait(msg):
    if AUTO is not None:
        print(f"  ... {msg} (auto {AUTO:g}s)")
        page.wait_for_timeout(AUTO * 1000)
    else:
        input(f"\n>>> ENTER for next: {msg} ")


def app(page):
    # Streamlit Cloud wraps the app in an iframe; locally it's the page itself
    fr = page.frame_locator("iframe[title='streamlitApp']")
    try:
        fr.locator("text=Invoice Agent").first.wait_for(timeout=3000)
        return fr
    except Exception:
        return page


def go_to(ui, name):
    ui.get_by_text(name, exact=True).first.click()
    page.wait_for_timeout(1500)


with sync_playwright() as p:
    try:
        browser = p.chromium.launch(channel="chrome", headless=HEADLESS, args=["--start-maximized"])
    except Exception:
        browser = p.chromium.launch(headless=HEADLESS, args=["--start-maximized"])
    page = browser.new_page(viewport={"width": 1440, "height": 860})
    print(f"Opening {URL}")
    page.goto(URL)
    page.wait_for_timeout(5000)
    ui = app(page)
    ui.get_by_text("Invoice Agent").first.wait_for(timeout=90000)  # cloud apps may need to wake up

    ui.get_by_role("button", name="Reset demo ledger").click()
    page.wait_for_timeout(2000)
    wait("start (intro on screen)")

    for i, (sample, label) in enumerate(STEPS):
        print(f"[{i+1}/{len(STEPS)}] {label}")
        go_to(ui, "Process invoice")
        page.mouse.move(900, 400)
        page.mouse.wheel(0, -3000)  # back to top
        page.wait_for_timeout(400)
        ui.get_by_role("combobox").first.click()
        page.wait_for_timeout(300)
        page.keyboard.type(sample.replace("_", " "), delay=15)
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1200)
        ui.get_by_role("button", name="▶ Run process").click()
        # run ids restart at 1 after the reset, so wait for *this* run's card
        ui.get_by_text(f"Run #{i + 1} ·").first.wait_for(timeout=120000)
        page.wait_for_timeout(1500)
        page.mouse.move(900, 500)
        page.mouse.wheel(0, 500)  # bring the decision card into view
        nxt = STEPS[i + 1][1] if i + 1 < len(STEPS) else "Review queue"
        wait(nxt)

    print("[review] approving the scanned invoice with a note")
    go_to(ui, "Review queue")
    box = ui.locator("[data-testid='stExpander']").filter(has_text="Shakti Logistics").first
    box.scroll_into_view_if_needed()
    page.wait_for_timeout(1500)
    box.get_by_role("textbox").fill("PO-4503 confirmed with buyer; 1.3% rate increase accepted")
    page.wait_for_timeout(1000)
    box.get_by_role("button", name="Approve").click()
    page.wait_for_timeout(2000)
    wait("Dashboard")

    go_to(ui, "Dashboard")
    page.wait_for_timeout(1500)
    wait("scroll to PO utilisation")
    page.mouse.move(900, 500)
    page.mouse.wheel(0, 900)
    wait("finish (closes browser)")
    browser.close()
    print("Done.")

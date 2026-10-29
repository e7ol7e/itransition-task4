"""Optional live UI checks: Playwright + installed Chrome, no live AI calls.

The chat response is stubbed to test its UI; audit queries use the real server.
Run with a Python environment containing Playwright: python browser_checks.py
"""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent


def main():
    output = ROOT / "submission"
    output.mkdir(exist_ok=True)
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("https://task4.evgeniy.win", wait_until="networkidle")
        overview = page.request.post(
            "https://task4.evgeniy.win/api/dashboard", data={"store": "STORE1"}
        )
        assert overview.ok
        winner = overview.json()["summary"]["best_buyers"][0]
        winner_amount = f"${winner['spending_cents'] / 100:,.2f}"
        winner_ids = winner["ids"]
        winner_label = ", ".join(map(str, winner_ids))
        page.locator('[data-proof="identity"]').click()
        expect(page.locator("#audit-title")).to_have_text("From records to real people")
        checks.append("Identity count drilldown")
        page.locator("#audit-content tbody .audit-trigger").first.click()
        expect(page.locator(".identity-graph .graph-id")).to_have_count(3)
        expect(page.locator(".field-match")).to_have_count(6)
        expect(page.locator(".field-different")).to_have_count(2)
        page.locator(".edge-button").nth(1).click()
        expect(page.locator(".edge-explanation")).to_contain_text("name changed")
        checks.append("Three-record graph, edge selection and matching/changed fields")
        with page.expect_download() as downloaded:
            page.get_by_role("button", name="Download this evidence page").click()
        downloaded.value.save_as(output / "evidence.json")
        assert (
            json.loads((output / "evidence.json").read_text())["result"]["kind"]
            == "person"
        )
        checks.append("Evidence download")
        page.keyboard.press("Escape")
        expect(page.locator("#audit-dialog")).not_to_be_visible()
        page.locator('[data-proof="day"]').first.click()
        expect(page.locator("#audit-title")).to_contain_text("Revenue on")
        page.locator(".audit-pagination button").last.click()
        expect(page.locator(".audit-pagination")).to_contain_text("26")
        checks.append("Daily source ledger, FX calculations and pagination")
        page.keyboard.press("Escape")
        page.locator('[data-proof="buyer"]').first.click()
        expect(page.locator("#audit-content .audit-stats")).to_contain_text(
            winner_amount
        )
        checks.append("Best-buyer reconciliation")
        page.keyboard.press("Escape")
        page.locator('[data-proof="authors"]').first.click()
        expect(page.locator("#audit-content .audit-stats")).to_contain_text("324")
        checks.append("Author set audit")
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Orders", exact=True).click()
        expect(page.locator("#records-view")).to_be_visible()
        page.locator("#filter-field").select_option("date")
        page.locator("#filter-op").select_option("eq")
        page.locator("#filter-value").fill("2025-08-20")
        page.get_by_role("button", name="Apply", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#view-status').textContent===''"
        )
        page.get_by_role("button", name="Overview", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#view-status').textContent===''"
        )
        page.locator('[data-proof="revenue"]').click()
        expect(page.locator("#audit-context")).to_contain_text("FILTERED VIEW")
        expect(page.locator("#audit-content .audit-stats")).to_contain_text("$3,719.35")
        checks.append("Drilldown respects active filters")
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Clear filters", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#view-status').textContent===''"
        )
        response = {
            "answer": "Verified count",
            "actions": [],
            "evidence": [
                {
                    "tool": "query_data",
                    "arguments": {
                        "store": "STORE1",
                        "dataset": "users",
                        "filters": [
                            {
                                "field": "email",
                                "op": "starts_with_any",
                                "value": ["s", "p"],
                            }
                        ],
                        "aggregates": [{"op": "count"}],
                    },
                    "result": {"rows": [{"row_count": 1}]},
                }
            ],
        }
        page.route("**/api/chat", lambda route: route.fulfill(json=response))
        page.locator("#chat-input").fill("Test evidence")
        page.locator("#chat-send").click()
        page.get_by_role("button", name="Inspect query & matching data").click()
        expect(page.locator("#audit-content")).to_contain_text("starts with any")
        expect(page.locator("#audit-content")).to_contain_text("count(rows)")
        checks.append("Chat answer opens real calculation with interpreted conditions")
        page.keyboard.press("Escape")
        page.unroute("**/api/chat")
        buyer_reply = {
            "answer": "Best buyer",
            "actions": [],
            "evidence": [
                {
                    "tool": "get_store_summary",
                    "arguments": {"store": "STORE1", "metrics": ["best_buyers"]},
                    "result": {"best_buyers": [{"ids": winner_ids}]},
                }
            ],
        }
        page.route("**/api/chat", lambda route: route.fulfill(json=buyer_reply))
        page.locator("#chat-input").fill("Inspect the winner")
        page.locator("#chat-send").click()
        page.get_by_role(
            "button", name=f"Explain buyer [{winner_label}]", exact=True
        ).click()
        expect(page.locator("#audit-content .identity-comparison")).to_contain_text(
            f"Record {winner_ids[0]}"
        )
        expect(page.locator("#audit-content .audit-stats")).to_contain_text(
            winner_amount
        )
        checks.append(
            "Chat winner opens its exact identity group, not unrelated groups"
        )
        page.keyboard.press("Escape")
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('[data-proof="person"]').first.click()
        expect(page.locator(".compact-graph")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.screenshot(path=str(output / "identity-mobile-final.png"))
        checks.append("Mobile identity graph, dialog and keyboard dismissal")
        assert not errors, errors
        browser.close()
    (output / "browser-checks.json").write_text(
        json.dumps({"passed": len(checks), "checks": checks}, indent=2) + "\n"
    )
    print("PASS", len(checks), "live browser checks")


if __name__ == "__main__":
    main()

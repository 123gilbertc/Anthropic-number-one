// Browser tests of the customer product against the real backend (synthetic demo slate).
// They prove engineering behaviour only; nothing here is evidence of predictive value.
import { APIRequestContext, Page, expect, test } from "@playwright/test";

const TOKEN = "e2e-token";
const bearer = { Authorization: `Bearer ${TOKEN}` };

async function api(request: APIRequestContext, method: "post" | "get", path: string, data?: unknown) {
  const r = await request[method](path, { headers: bearer, data });
  expect(r.ok(), `${path}: ${await r.text()}`).toBeTruthy();
  return r.json();
}

let n = 0;
async function signUp(page: Page) {
  const email = `e2e-${Date.now()}-${n++}@example.com`;
  await page.goto("/#/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("e2e password 123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByTestId("account-link")).toBeVisible();
  return email;
}

/** Advance the replay until the backend reports a paper-entry-eligible contract. */
async function stepToEligible(request: APIRequestContext): Promise<{ gameId: string; contractId: string }> {
  for (let i = 0; i < 300; i++) {
    const b = await api(request, "get", "/api/board?mode=demo");
    for (const r of b.rows) {
      if (r.value_side?.state === "PAPER ENTRY ELIGIBLE") return { gameId: r.game_id, contractId: r.value_side.contract_id };
    }
    const info = await api(request, "post", "/api/session/step", { n: 40 });
    if (info.finished) break;
  }
  throw new Error("no eligible paper entry in the demo slate");
}

async function stepTo(request: APIRequestContext, iso: string) {
  for (let i = 0; i < 200; i++) {
    const s = await api(request, "get", "/api/session");
    if (s.as_of >= iso || s.finished) return;
    await api(request, "post", "/api/session/step", { n: 400 });
  }
}

test.beforeEach(async ({ request, page }) => {
  await api(request, "post", "/api/session", { fixture: "slate_synthetic.jsonl", mode: "mechanics" });
  await page.addInitScript(() => { try { localStorage.setItem("te.mode", "demo"); } catch { /* */ } });
});

test("live mode shows unknown coverage, never demo data or a fake empty slate", async ({ page }) => {
  await page.goto("/#/board");
  await page.getByTestId("mode-live").click();
  await expect(page.getByTestId("mode-ribbon")).toContainText("LIVE DATA MODE");
  for (const s of ["NHL", "NFL", "TENNIS", "MLB"]) {
    await expect(page.getByTestId(`coverage-${s}`)).toContainText("Game count unknown");
  }
  await expect(page.getByText("This is not an empty slate")).toBeVisible();
  await expect(page.getByTestId("game-list").locator("a")).toHaveCount(0);
});

test("demo board lists every sport, keeps unavailable games, and labels synthetic models", async ({ page, request }) => {
  await stepTo(request, "2026-10-11T18:15:00");
  await page.goto("/#/board");
  await expect(page.getByTestId("mode-ribbon")).toContainText("DEMO");
  const list = page.getByTestId("game-list");
  for (const id of ["SYN-NHL-1", "SYN-NFL-1", "SYN-TEN-2", "SYN-MLB-1", "SYN-TEN-4"]) {
    await expect(page.getByTestId(`row-${id}`)).toBeVisible();
  }
  await expect(page.getByTestId("row-SYN-TEN-4")).toContainText("No market mapped");
  await expect(page.getByTestId("row-SYN-TEN-3")).toContainText("Doubles model not enabled");
  await expect(page.getByTestId("row-SYN-NHL-1")).toContainText("Synthetic-only model");
  await page.getByTestId("sport-TENNIS").click();
  await expect(list.locator("a")).toHaveCount(4);
  await page.getByTestId("sport-ALL").click();
  await page.getByTestId("board-search").fill("Lynx");
  await expect(list.locator("a")).toHaveCount(1);
});

test("rows keep their place while values update until the user re-sorts", async ({ page, request }) => {
  await stepTo(request, "2026-10-11T17:50:00");
  await page.goto("/#/board");
  await page.getByTestId("board-sort").selectOption("probability");
  const ids = async () => page.getByTestId("game-list").locator("a").evaluateAll((as) => as.map((a) => a.getAttribute("data-testid")));
  await expect.poll(async () => (await ids()).length).toBeGreaterThan(4);
  const before = await ids();
  await api(request, "post", "/api/session/step", { n: 1500 });  // probabilities move
  await page.waitForTimeout(1500);
  expect(await ids()).toEqual(before);  // no row moved under the cursor
});

test("full workflow: open game, explain, preview, one paper order despite a double click, portfolio", async ({ page, request }) => {
  await signUp(page);
  const { gameId, contractId } = await stepToEligible(request);
  await page.goto("/#/board");
  await page.getByTestId(`row-${gameId}`).click();
  await expect(page.getByTestId("workspace")).toBeVisible();
  await expect(page.getByTestId("q-likely")).toBeVisible();
  await expect(page.getByTestId("prob-price-chart")).toBeVisible();
  await page.getByTestId("why-btn").click();
  await expect(page.getByTestId("evidence-drawer")).toContainText("not proof of causation");
  await page.getByRole("button", { name: "Close" }).first().click();
  const side = await (await request.get(`/api/events/${gameId}/workspace`)).json();
  const participant = side.contracts.find((c: any) => c.contract_id === contractId).participant;
  await page.getByTestId(`side-${participant}`).click();
  await expect(page.getByTestId("ev-plain")).toContainText("not a promised profit");
  await page.getByTestId("preview-btn").click();
  const preview = page.getByTestId("preview");
  await expect(preview).toContainText(/Eligible|Not eligible/);
  if (await preview.getByText("Not eligible right now").count()) test.skip(true, "signal moved on before the click");
  await page.getByTestId("order-btn").dblclick();
  await expect(page.getByTestId("order-status")).toBeVisible({ timeout: 10_000 });
  await api(request, "post", "/api/session/step", { n: 30 });  // delay passes; backend re-checks and fills
  await page.goto("/#/portfolio");
  await expect(page.getByTestId("orders-table").locator("tbody tr")).toHaveCount(1);
  // the fill arrives over this account's private update stream, without a reload
  await expect(page.getByTestId("orders-table")).toContainText(/FILLED|PARTIAL|REJECTED/, { timeout: 15_000 });
});

test("a second account never sees the first account's paper orders", async ({ browser, request }) => {
  const a = await browser.newPage();
  await signUp(a);
  const { gameId, contractId } = await stepToEligible(request);
  const r = await a.request.post("/api/paper/preview", { headers: { "X-SE-Request": "1" }, data: { game_id: gameId, contract_id: contractId } });
  const p = await r.json();
  test.skip(!p.eligible, "signal moved on");
  const o = await a.request.post("/api/paper/orders", { headers: { "X-SE-Request": "1" },
    data: { decision_id: p.decision.decision_id, idempotency_key: "k-a", expected_contract_id: contractId } });
  expect(o.ok()).toBeTruthy();
  const b = await browser.newPage();
  await signUp(b);
  await b.goto("/#/portfolio");
  await expect(b.getByText("No paper orders yet")).toBeVisible();
  const led = await (await b.request.get("/api/paper/ledger")).json();
  expect(led.orders).toHaveLength(0);
});

test("a slow preview for a contract the user has left is never shown under the new one", async ({ page, request }) => {
  await signUp(page);
  const { gameId, contractId } = await stepToEligible(request);
  const ws = await (await request.get(`/api/events/${gameId}/workspace`)).json();
  const mine = ws.contracts.find((c: any) => c.contract_id === contractId).participant;
  const other = ws.contracts.find((c: any) => c.contract_id !== contractId).participant;
  await page.goto(`/#/game/${gameId}`);
  await page.getByTestId(`side-${mine}`).click();
  let released = false;
  await page.route("**/api/paper/preview", async (route) => {
    await new Promise((r) => setTimeout(r, 2500));
    released = true;
    await route.continue();
  });
  await page.getByTestId("preview-btn").click();
  await page.getByTestId(`side-${other}`).click();
  await expect.poll(() => released, { timeout: 10_000 }).toBe(true);
  await page.waitForTimeout(800);
  await expect(page.getByTestId("order-btn")).toHaveCount(0);
  await page.unroute("**/api/paper/preview");
});

test("old dashboard routes redirect to their new homes", async ({ page }) => {
  await page.goto("/#tracker");
  await expect(page.getByRole("heading", { name: "Paper Portfolio" })).toBeVisible();
  await page.goto("/#evaluation");
  await expect(page.getByRole("heading", { name: "Performance" })).toBeVisible();
  await page.goto("/#/research/coverage");
  await expect(page.getByTestId("coverage-registry")).toBeVisible();
});

test("keyboard: a game row is reachable and opens with Enter", async ({ page, request }, info) => {
  test.skip(info.project.name === "mobile", "keyboard path is a desktop concern");
  await stepTo(request, "2026-10-11T18:00:00");
  await page.goto("/#/board");
  const row = page.getByTestId("row-SYN-NHL-1");
  await row.focus();
  await expect(row).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("workspace")).toBeVisible();
});

test("stream reconnect after the server replaces the session refreshes the board", async ({ page, request }) => {
  await page.goto("/#/research");
  await expect(page.getByTestId("stream-status")).toBeVisible();
  const before = await api(request, "get", "/api/session");
  await expect(page.getByTestId("session-info")).toContainText(before.run_id);
  await api(request, "post", "/api/session", { fixture: "slate_synthetic.jsonl", mode: "mechanics" });
  await api(request, "post", "/api/session/step", { n: 50 });
  const after = await api(request, "get", "/api/session");
  await expect(page.getByTestId("session-info")).toContainText(after.run_id, { timeout: 20_000 });
});

test("no horizontal page overflow on the main screens", async ({ page, request }) => {
  await stepTo(request, "2026-10-11T18:10:00");
  for (const path of ["/#/board", "/#/game/SYN-TEN-2", "/#/game/SYN-MLB-1", "/#/portfolio", "/#/performance", "/#/research", "/#/settings"]) {
    await page.goto(path);
    await page.waitForTimeout(700);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${path} overflows`).toBeLessThanOrEqual(1);
  }
});

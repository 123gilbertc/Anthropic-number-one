import { APIRequestContext, Page, expect, test } from "@playwright/test";

const TOKEN = "e2e-token";
const bearer = { Authorization: `Bearer ${TOKEN}` };

async function api(request: APIRequestContext, method: "post", path: string, data?: unknown) {
  const r = await request[method](path, { headers: bearer, data });
  expect(r.ok(), `${path}: ${await r.text()}`).toBeTruthy();
  return r.json();
}

async function signIn(page: Page) {
  await page.getByLabel("Operator token").fill(TOKEN);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("OPERATOR SIGNED IN")).toBeVisible();
}

// Advance replay (harness shortcut via the same authenticated API) until the
// backend reports an eligible signal; returns that contract id.
async function stepToSignal(request: APIRequestContext): Promise<string> {
  for (let i = 0; i < 400; i++) {
    const games = await (await request.get("/api/games")).json();
    const c = games[0].contracts.find((k: any) => k.signal_eligible);
    if (c) return c.contract_id;
    const info = await api(request, "post", "/api/session/step", { n: 5 });
    if (info.finished) break;
  }
  throw new Error("no eligible signal in replay");
}

// Tests share one backend: start each from a fresh honest replay session.
test.beforeEach(async ({ request }) => {
  await api(request, "post", "/api/session", { mode: "honest" });
});

test("honest mode: real statuses, blocked live path, no fake values", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByTestId("banners")).toContainText("SYNTHETIC DEMO");
  await expect(page.getByTestId("banners")).toContainText("LIVE GAME FEED: NOT CONNECTED");
  await expect(page.getByTestId("banners")).toContainText("NO MODEL LOADED");
  await expect(page.getByTestId("stream-status")).toContainText("CONNECTED");
  await page.getByRole("button", { name: "Connections" }).click();
  await expect(page.getByTestId("status-nhl_live_feed")).toHaveText("BLOCKED");
  await expect(page.getByTestId("status-kalshi_ws")).toHaveText("NOT_CONFIGURED");
  await expect(page.getByTestId("ready-Live value signals")).toContainText("BLOCKED");
  // no "Connected" badge for anything without a successful real test
  const conns = await (await request.get("/api/connections")).json();
  for (const p of conns.providers) {
    if (!p.last_test?.ok) expect(p.status, p.id).not.toBe("CONNECTED");
    await expect(page.getByTestId(`status-${p.id}`)).toHaveText(p.status);
  }
});

test("unavailable provider shows the real error, never a fake Connected", async ({ page }) => {
  await page.goto("/");
  await signIn(page);
  await page.getByRole("button", { name: "Connections" }).click();
  await page.getByTestId("test-btn-kalshi_rest").click();
  const status = page.getByTestId("status-kalshi_rest");
  await expect(status).toHaveText(/^(CONNECTED|FAILED)$/, { timeout: 30_000 });
  if ((await status.textContent()) === "FAILED") {
    await expect(page.getByTestId("test-kalshi_rest")).toContainText("FAILED");  // real error shown
  }
  await page.getByTestId("provider-kalshi_rest").getByRole("button", { name: "Disconnect" }).click();
  await expect(status).toHaveText("DISCONNECTED");
});

test("full workflow: signal -> preview -> one order despite double click -> ledger -> outcome", async ({ page, request }) => {
  await api(request, "post", "/api/session", { mode: "mechanics" });
  await page.goto("/");
  await signIn(page);
  await expect(page.getByTestId("banners")).toContainText("MECHANICS DEMO");
  const contract = await stepToSignal(request);
  await page.getByRole("button", { name: "Game", exact: true }).click();
  await page.getByTestId(`tile-${contract}`).click();
  await expect(page.getByTestId("decision")).toContainText("eligible paper signal");
  await page.getByTestId("preview-btn").click();
  await expect(page.getByTestId("preview")).toContainText("ELIGIBLE");
  // double click: same idempotency key -> exactly one order
  await page.getByTestId("order-btn").dblclick();
  await expect(page.getByTestId("order-status")).toContainText("PENDING");
  let ledger = await (await request.get("/api/paper/ledger")).json();
  expect(ledger.orders.length).toBe(1);
  // a click is not a fill: the fill happens only after replay time passes the delay
  expect(ledger.orders[0].fill).toBeNull();
  await api(request, "post", "/api/session/step", { n: 5000 });
  // the UI updates from backend events (no reload)
  await expect(page.getByTestId("order-status")).toContainText(/FILLED|PARTIAL|REJECTED/, { timeout: 15_000 });
  await page.getByRole("button", { name: "Paper tracker" }).click();
  await expect(page.getByTestId("ledger-events")).toContainText("ORDER_ACCEPTED");
  ledger = await (await request.get("/api/paper/ledger")).json();
  const o = ledger.orders[0];
  if (o.fill) {
    await expect(page.getByTestId("ledger-events")).toContainText("POSITION_SETTLED");
    await page.getByRole("button", { name: "Evaluation" }).click();
    await expect(page.getByText("Filled-position win rate")).toBeVisible();
  }
  // isolation: every record is labelled as replay/synthetic
  expect(ledger.events.every((e: any) => e.mode === "REPLAY" && e.data_label === "SYNTHETIC")).toBe(true);
});

test("expired signal is refused by the backend", async ({ page, request }) => {
  await api(request, "post", "/api/session", { mode: "mechanics" });
  await page.goto("/");
  await signIn(page);
  const contract = await stepToSignal(request);
  await page.getByRole("button", { name: "Game", exact: true }).click();
  await page.getByTestId(`tile-${contract}`).click();
  await page.getByTestId("preview-btn").click();
  await expect(page.getByTestId("preview")).toContainText("ELIGIBLE");
  await api(request, "post", "/api/session/step", { n: 60 });  // move past expiry
  await expect(page.getByTestId("preview")).toContainText("EXPIRED", { timeout: 10_000 });
  // even if a stale button were clicked, the backend decides
  const sig = await (await request.get("/api/signals")).json();
  const r = await request.post("/api/paper/orders", { headers: bearer, data: {
    decision_id: sig[0].decision.decision_id, idempotency_key: "late-click" } });
  expect([409, 410]).toContain(r.status());
});

test("stream reconnect reconciles state after the server replaces the session", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByTestId("stream-status")).toContainText("CONNECTED");
  const before = await (await request.get("/api/session")).json();
  await expect(page.getByTestId("session-info")).toContainText(before.run_id);
  // the server ends this stream (session replaced); events continue while the client reconnects
  await api(request, "post", "/api/session", { mode: "honest" });
  await api(request, "post", "/api/session/step", { n: 50 });
  const after = await (await request.get("/api/session")).json();
  expect(after.run_id).not.toBe(before.run_id);
  // the client reconnects and refetches: no stale copy of the old run survives
  await expect(page.getByTestId("session-info")).toContainText(after.run_id, { timeout: 15_000 });
  await expect(page.getByTestId("session-info")).toContainText(`${after.position}/`);
  await expect(page.getByTestId("stream-status")).toContainText("CONNECTED");
});

test("layout has no horizontal page overflow", async ({ page }) => {
  await page.goto("/");
  for (const tab of ["Connections", "Games", "Game", "Paper tracker", "Evaluation", "Audit"]) {
    await page.getByRole("button", { name: tab, exact: true }).click();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${tab} overflows`).toBeLessThanOrEqual(1);
  }
});

// SPDX-License-Identifier: GPL-3.0-only
const { chromium } = require("playwright-core");
const fs = require("node:fs");
const net = require("node:net");

async function main() {
  const url = new URL(process.argv[2] || "http://127.0.0.1:18080/");
  const octets = url.hostname.split(".").map(Number);
  const privateAddress = net.isIP(url.hostname) === 4 && (octets[0] === 10 ||
    (octets[0] === 192 && octets[1] === 168) ||
    (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31));
  const secured = url.protocol === "https:" && privateAddress;
  if (!secured && (url.protocol !== "http:" || url.hostname !== "127.0.0.1")) {
    throw new Error("live smoke test requires localhost HTTP or private-LAN HTTPS");
  }
  const httpCredentials = secured && process.env.GATEWAY_AUTH_FILE ? {
    origin: url.origin, username: process.env.GATEWAY_AUTH_USER || "tesla",
    password: fs.readFileSync(process.env.GATEWAY_AUTH_FILE, "utf8").trim() } : undefined;
  const executablePath = process.env.EDGE_PATH ||
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ viewport: process.env.SMOKE_MOBILE ?
      { width: 390, height: 844 } : { width: 1440, height: 900 },
    httpCredentials, ignoreHTTPSErrors: secured });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error));
    await page.goto(url.href, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => {
      const image = document.querySelector("#image");
      return image && image.naturalWidth > 0 && image.naturalHeight > 0;
    }, null, { timeout: 30000 });
    const frame = await page.locator("#image").evaluate((image) => ({
      width: image.naturalWidth,
      height: image.naturalHeight,
    }));
    const touchLayer = await page.locator("#touch-layer").boundingBox();
    if (!touchLayer || !touchLayer.width || !touchLayer.height) {
      throw new Error("touch layer is not visible");
    }
    if (pageErrors.length) throw pageErrors[0];
    if (process.env.SMOKE_SCREENSHOT_PATH) {
      await page.screenshot({ path: process.env.SMOKE_SCREENSHOT_PATH });
    }
    console.log(`live frontend frame ${frame.width}x${frame.height}; touch layer ${touchLayer.width}x${touchLayer.height}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

/**
 * Kling AI Image-to-Video generator
 * Run on your LOCAL machine (not this server):
 *   node generate-clips.mjs
 *
 * Place your 7 photos as photo1.jpg–photo7.jpg in the same folder as this script.
 * Generated clips will be saved to ./clips/clip1.mp4–clip7.mp4
 * Then copy the clips/ folder to: prompt-to-video/public/clips/
 */

import crypto from "crypto";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Credentials ───────────────────────────────────────────────────
const ACCESS_KEY = "ArrneHDgDC8aQPPDbDYGPankff8TRhrY";
const SECRET_KEY = "RdFmCf4QGrMy4DgDdyYthLfFmMnLkY8C";

// ── Config ────────────────────────────────────────────────────────
const PHOTOS = Array.from({ length: 7 }, (_, i) => `photo${i + 1}.jpg`);
const OUTPUT_DIR = path.join(__dirname, "clips");
const POLL_INTERVAL_MS = 5000;
const MAX_WAIT_MS = 300000; // 5 minutes per clip

// ── JWT generation (Kling uses HS256) ────────────────────────────
function generateJWT() {
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const payload = Buffer.from(
    JSON.stringify({ iss: ACCESS_KEY, exp: now + 1800, nbf: now - 5 })
  ).toString("base64url");
  const sig = crypto
    .createHmac("sha256", SECRET_KEY)
    .update(`${header}.${payload}`)
    .digest("base64url");
  return `${header}.${payload}.${sig}`;
}

// ── Upload image as base64 data URI ──────────────────────────────
function imageToDataUri(filePath) {
  const ext = path.extname(filePath).slice(1).toLowerCase();
  const mime = ext === "jpg" || ext === "jpeg" ? "image/jpeg" : `image/${ext}`;
  const data = fs.readFileSync(filePath).toString("base64");
  return `data:${mime};base64,${data}`;
}

// ── API helpers ──────────────────────────────────────────────────
async function apiPost(endpoint, body) {
  const token = generateJWT();
  const res = await fetch(`https://api.klingai.com${endpoint}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function apiGet(endpoint) {
  const token = generateJWT();
  const res = await fetch(`https://api.klingai.com${endpoint}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.json();
}

// ── Submit image-to-video job ────────────────────────────────────
async function submitJob(photoPath) {
  const imageDataUri = imageToDataUri(photoPath);
  const result = await apiPost("/v1/videos/image2video", {
    model_name: "kling-v1",
    image: imageDataUri,
    prompt: "fast paced roofing construction, workers on roof, dynamic camera movement, cinematic",
    negative_prompt: "blurry, slow motion, static",
    cfg_scale: 0.5,
    mode: "std",
    duration: "5",
  });

  if (!result?.data?.task_id) {
    throw new Error(`Submit failed: ${JSON.stringify(result)}`);
  }
  return result.data.task_id;
}

// ── Poll until complete ──────────────────────────────────────────
async function pollJob(taskId) {
  const start = Date.now();
  while (Date.now() - start < MAX_WAIT_MS) {
    const result = await apiGet(`/v1/videos/image2video/${taskId}`);
    const status = result?.data?.task_status;
    console.log(`  Task ${taskId}: ${status}`);

    if (status === "succeed") {
      const url = result?.data?.task_result?.videos?.[0]?.url;
      if (!url) throw new Error("No video URL in result");
      return url;
    }
    if (status === "failed") {
      throw new Error(`Task failed: ${JSON.stringify(result?.data?.task_status_msg)}`);
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  throw new Error(`Timed out waiting for task ${taskId}`);
}

// ── Download video ───────────────────────────────────────────────
async function downloadVideo(url, outPath) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const buffer = await res.arrayBuffer();
  fs.writeFileSync(outPath, Buffer.from(buffer));
}

// ── Main ─────────────────────────────────────────────────────────
async function main() {
  if (!fs.existsSync(OUTPUT_DIR)) fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  for (let i = 0; i < PHOTOS.length; i++) {
    const photoFile = path.join(__dirname, PHOTOS[i]);
    if (!fs.existsSync(photoFile)) {
      console.error(`✗ Missing: ${PHOTOS[i]} — skipping`);
      continue;
    }

    const outFile = path.join(OUTPUT_DIR, `clip${i + 1}.mp4`);
    if (fs.existsSync(outFile)) {
      console.log(`✓ clip${i + 1}.mp4 already exists, skipping`);
      continue;
    }

    console.log(`\n[${i + 1}/${PHOTOS.length}] Submitting ${PHOTOS[i]}...`);
    const taskId = await submitJob(photoFile);
    console.log(`  Task ID: ${taskId}`);
    console.log(`  Polling for completion...`);
    const videoUrl = await pollJob(taskId);
    console.log(`  Downloading...`);
    await downloadVideo(videoUrl, outFile);
    console.log(`✓ Saved: clips/clip${i + 1}.mp4`);
  }

  console.log("\n✅ All done! Copy the clips/ folder to:");
  console.log("   prompt-to-video/public/clips/");
  console.log("Then render with:");
  console.log("   npx remotion render RoofVideo out/roof-video.mp4 --concurrency=1");
}

main().catch((err) => {
  console.error("Fatal:", err.message);
  process.exit(1);
});

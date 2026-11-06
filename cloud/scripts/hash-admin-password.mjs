#!/usr/bin/env node
/**
 * Offline helper: print ADMIN_PASSWORD_HASH for wrangler secret put.
 * Usage: node scripts/hash-admin-password.mjs
 * Reads password from stdin (no argv — avoids shell history).
 * Never commit the plaintext password or this script's output into git secrets.
 */
import { createHash, randomBytes, pbkdf2Sync } from "node:crypto";
import { createInterface } from "node:readline";

// Workers reject PBKDF2 iterations above 100_000.
const ITERATIONS = 100_000;

function b64url(buf) {
  return Buffer.from(buf)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

async function readPassword() {
  if (process.stdin.isTTY) {
    process.stderr.write("Password (hidden input not available — type and Enter): ");
  }
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  const line = await new Promise((resolve) => rl.question("", resolve));
  rl.close();
  return String(line ?? "").replace(/\r?\n$/, "");
}

const password = await readPassword();
if (!password) {
  console.error("empty password");
  process.exit(1);
}
const salt = randomBytes(16);
const hash = pbkdf2Sync(password, salt, ITERATIONS, 32, "sha256");
const encoded = `v1$pbkdf2-sha256$${ITERATIONS}$${b64url(salt)}$${b64url(hash)}`;
// Fingerprint only — never echo the password.
const fp = createHash("sha256").update(encoded).digest("hex").slice(0, 12);
process.stdout.write(`${encoded}\n`);
process.stderr.write(`hash fingerprint ${fp} (safe to log)\n`);

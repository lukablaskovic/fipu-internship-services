// Run against a local PDF connector with node test-retry.mjs.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { setTimeout } from "node:timers/promises";
import axios from "axios";

const service = "http://127.0.0.1:8084";
const headers = { "Idempotency-Key": randomUUID() };
const data = { detaljan_opis_zadatka: "Synthetic retry check" };
const generate = () => axios.post(service + "/api/potvrda", data, { headers });
const first = (await generate()).data.pdf_attachment_url;
await setTimeout(1200); // A new Chromium render would include a different creation timestamp.
assert.equal((await generate()).data.pdf_attachment_url, first);
const { pathname } = new URL(first);
const pdf = await axios.get(service + pathname, { responseType: "arraybuffer" });
assert.equal(pathname, "/api/potvrda/" + createHash("sha256").update(pdf.data).digest("hex") + ".pdf");
console.log("PASS: retry returned the original certificate.");

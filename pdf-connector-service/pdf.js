import { Router } from "express";
import { chromium } from "playwright";
import { promises as fs } from "fs";
import path from "path";
import { fileURLToPath } from "url";
import crypto from "crypto";
import "dotenv/config";

const router = Router();

// Utility functions
function replacePlaceholders(template, data) {
  for (const key in data) {
    const placeholder = `$${key}`;
    const value = data[key];
    template = template.replace(placeholder, value);
  }
  return template;
}

async function generatePDFContent(page, jsonData) {
  // Use long template if task description exceeds 700 characters
  const taskDescription = jsonData.detaljan_opis_zadatka || "";
  const templateFile =
    taskDescription.length > 700 ? "index-long.html" : "index.html";

  const htmlTemplate = await fs.readFile(templateFile, "utf8");
  let htmlContent = replacePlaceholders(htmlTemplate, jsonData);
  if (process.env.PDF_LOGO_PATH) {
    const logo = await fs.readFile(process.env.PDF_LOGO_PATH);
    htmlContent = htmlContent.replaceAll(
      "https://fipu.unipu.hr/_pub/themes_static/unipu2020/fipu/icons/fipu_hr.png",
      `data:image/png;base64,${logo.toString("base64")}`
    );
  }
  await page.setContent(htmlContent);
}

async function applyStyles(page) {
  await page.addStyleTag({ content: "@page { margin: 0cm; }" });
  await page.addStyleTag({ path: "css/katex.min.css" });
  await page.addStyleTag({ path: "css/qcss.css" });
  await page.addStyleTag({ path: "css/tailwind.css" });
  await page.addStyleTag({
    content:
      "* {print-color-adjust: exact; -webkit-print-color-adjust: exact;}",
  });
}

async function renderCertificate(jsonData) {
  const browser = await chromium.launch({
    args: [
      "--no-sandbox",
      "--headless",
      "--hide-scrollbars",
      "--printBackground=true",
      "--disable-dev-shm-usage",
      "--font-render-hinting=medium",
    ],
  });

  try {
    const page = await browser.newPage();
    await generatePDFContent(page, jsonData);
    await applyStyles(page);
    await page.emulateMedia({ media: "screen" });
    return await page.pdf({ format: "A4" });
  } finally {
    await browser.close();
  }
}

const directory = path.join(path.dirname(fileURLToPath(import.meta.url)), "potvrde");
const digest = bytes => crypto.createHash("sha256").update(bytes).digest("hex");

async function certificateName(data, key) {
  // Publish complete files atomically. A hidden hard link keeps the original bytes across retries.
  const receipt = key && path.join(directory, "." + digest(JSON.stringify([key, data])));
  if (receipt) {
    try {
      const name = digest(await fs.readFile(receipt)) + ".pdf";
      await publishLink(receipt, path.join(directory, name));
      return name;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  const bytes = await renderCertificate(data);
  const temp = path.join(directory, "." + crypto.randomUUID());
  await fs.writeFile(temp, bytes, { flag: "wx" });
  try {
    if (receipt) await publishLink(temp, receipt);
    const name = digest(receipt ? await fs.readFile(receipt) : bytes) + ".pdf";
    await publishLink(receipt || temp, path.join(directory, name));
    return name;
  } finally {
    await fs.unlink(temp);
  }
}

async function publishLink(source, target) {
  try {
    await fs.link(source, target);
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
  }
}

router.post("/potvrda", async (req, res) => {
  try {
    const name = await certificateName(req.body, req.get("Idempotency-Key"));
    res.json({ pdf_attachment_url: `${process.env.SERVICE_URL}/api/potvrda/${name}` });
  } catch (error) {
    console.error(error);
    res.status(500).send("Internal Server Error");
  }
});

export default router;

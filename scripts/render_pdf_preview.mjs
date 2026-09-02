import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [pdfPath, outputDir, dependenciesDir] = process.argv.slice(2);
if (!pdfPath || !outputDir || !dependenciesDir) {
  throw new Error("usage: render_pdf_preview.mjs PDF OUTPUT_DIR NODE_MODULES_DIR");
}

const require = createRequire(path.join(dependenciesDir, "package.json"));
const { createCanvas } = require("@napi-rs/canvas");
const pdfjs = await import(
  pathToFileURL(path.join(dependenciesDir, "pdfjs-dist", "legacy", "build", "pdf.mjs")).href
);

fs.mkdirSync(outputDir, { recursive: true });
const document = await pdfjs.getDocument({
  data: new Uint8Array(fs.readFileSync(pdfPath)),
  disableWorker: true,
}).promise;

for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
  const page = await document.getPage(pageNumber);
  const viewport = page.getViewport({ scale: 1.25 });
  const canvas = createCanvas(Math.ceil(viewport.width), Math.ceil(viewport.height));
  const context = canvas.getContext("2d");
  await page.render({ canvasContext: context, viewport }).promise;
  const filename = `page-${String(pageNumber).padStart(2, "0")}.png`;
  fs.writeFileSync(path.join(outputDir, filename), canvas.toBuffer("image/png"));
}

process.stdout.write(`pages=${document.numPages}\n`);

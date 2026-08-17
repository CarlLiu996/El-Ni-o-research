import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const cli = process.env.WIND_MCP_CLI ?? path.join(os.homedir(), ".agents", "skills", "wind-mcp-skill", "scripts", "cli.mjs");
const outDir = path.resolve("data", "processed", "fundamentals");
const vintage = new Date().toISOString().slice(0, 10);
fs.mkdirSync(outDir, { recursive: true });

const specs = [
  ["mpob_production", "S5022944", "马来西亚:产量:棕榈油", "tonnes"],
  ["mpob_inventory", "S5022948", "马来西亚:库存:棕榈油", "tonnes"],
  ["mpob_exports", "S5022955", "马来西亚:出口数量:棕榈油", "tonnes"],
  ["india_sugar_output", "Z9808072", "印度:食糖产量", "100kt"],
  ["unica_crush", "S5201214", "巴西中南部:甘蔗压榨量", "tonnes"],
  ["unica_sugar_mix", "W5495204", "巴西:中南部地区:制糖比(榨季)", "pct"],
  ["global_sugar_ending_stocks_source", "S5010514", "全球:期末库存量:糖", "1000 tonnes"],
  ["global_sugar_total_use_source", "S5010513", "全球:总消费量:糖", "1000 tonnes"],
];
const fetched = new Map();

function quoteCsv(value) {
  const text = value == null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function findSeries(value, code) {
  if (!value || typeof value !== "object") return null;
  if (value.meta?.code === code && Array.isArray(value.date) && Array.isArray(value.value)) return value;
  for (const child of Object.values(value)) {
    const found = findSeries(child, code);
    if (found) return found;
  }
  return null;
}

function isoDate(raw) {
  const text = String(raw ?? "");
  return /^\d{8}$/.test(text) ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}` : text;
}

for (const [dataset, code, label, unit] of specs) {
  const params = { executionMode: "fetch", question: `${code} ${label}`, beginDate: "20150101", endDate: vintage.replaceAll("-", "") };
  const stdout = execFileSync(process.execPath, [cli, "call", "economic_data", "natural_language_get_edb_data", JSON.stringify(params)], { encoding: "utf8", maxBuffer: 100 * 1024 * 1024 });
  const outer = JSON.parse(stdout);
  if (outer.ok === false) throw new Error(`${dataset}: ${JSON.stringify(outer.error)}`);
  const toolText = (outer.result?.content ?? outer.content)?.find?.((item) => item.type === "text")?.text;
  const payload = toolText ? JSON.parse(toolText) : (outer.result ?? outer);
  const series = findSeries(payload, code);
  if (!series) throw new Error(`${dataset}: Wind EDB series ${code} not found`);
  const publishedAt = isoDate(series.meta?.updateDate ?? vintage);
  const rows = series.date.map((date, index) => [
    isoDate(date), series.value[index], unit, series.meta?.enSource ?? "Wind EDB", publishedAt, vintage, code, label,
  ]);
  const csv = [["date", "value", "unit", "source", "published_at", "vintage", "indicator_code", "indicator_name"], ...rows]
    .map((row) => row.map(quoteCsv).join(",")).join("\n") + "\n";
  fs.writeFileSync(path.join(outDir, `${dataset}.csv`), csv, "utf8");
  fetched.set(dataset, rows);
  console.log(`${dataset}: ${rows.length} rows; latest ${rows.at(-1)?.[0]}`);
}

const ending = fetched.get("global_sugar_ending_stocks_source") ?? [];
const totalUse = new Map((fetched.get("global_sugar_total_use_source") ?? []).map((row) => [row[0], Number(row[1])]));
const ratios = ending.filter((row) => totalUse.has(row[0]) && totalUse.get(row[0]) !== 0).map((row) => [
  row[0], Number(row[1]) / totalUse.get(row[0]), "ratio", "U.S. Department of Agriculture via Wind EDB", row[4], vintage,
  "S5010514/S5010513", "全球糖库销比（期末库存/总消费量）", row[1], totalUse.get(row[0]),
]);
const ratioCsv = [["date", "value", "unit", "source", "published_at", "vintage", "indicator_code", "indicator_name", "ending_stocks_1000t", "total_use_1000t"], ...ratios]
  .map((row) => row.map(quoteCsv).join(",")).join("\n") + "\n";
fs.writeFileSync(path.join(outDir, "global_sugar_stock_use.csv"), ratioCsv, "utf8");
console.log(`global_sugar_stock_use: ${ratios.length} rows; latest ${ratios.at(-1)?.[0]}`);

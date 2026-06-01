import { readFileSync, writeFileSync } from "node:fs";

interface CsvRow {
  rank: number;
  article: string;
  word: string;
  translation: string;
  partOfSpeech: string;
}

const [inputPath, outputPath] = process.argv.slice(2);

if (!inputPath || !outputPath) {
  throw new Error("Usage: tsx scripts/import-adsonant-spanish.ts <input.csv> <output.json>");
}

const csv = readFileSync(inputPath, "utf8").replace(/^\uFEFF/, "");
const [, ...lines] = csv.trim().split(/\r?\n/);
const rows = lines.slice(0, 1000).map(parseRow);

writeFileSync(outputPath, `${JSON.stringify(rows, null, 2)}\n`);

console.log(`Wrote ${rows.length} Spanish frequency rows to ${outputPath}`);

function parseRow(line: string): CsvRow {
  const [rank, article, word, translation, partOfSpeech] = parseCsvLine(line);

  return {
    rank: Number(rank),
    article,
    word,
    translation,
    partOfSpeech,
  };
}

function parseCsvLine(line: string) {
  const cells: string[] = [];
  let cell = "";
  let inQuotes = false;

  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    const next = line[index + 1];

    if (character === '"' && next === '"') {
      cell += character;
      index += 1;
      continue;
    }

    if (character === '"') {
      inQuotes = !inQuotes;
      continue;
    }

    if (character === "," && !inQuotes) {
      cells.push(cell);
      cell = "";
      continue;
    }

    cell += character;
  }

  cells.push(cell);

  if (cells.length !== 5) {
    throw new Error(`Expected 5 CSV cells, got ${cells.length}: ${line}`);
  }

  return cells.map((value) => value.trim());
}

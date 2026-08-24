// orval tags-split 不产 endpoints 根 barrel;此脚本在 generate 后自动补齐。
import { readdirSync, statSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "generated", "endpoints");
const tags = readdirSync(root).filter((d) => statSync(join(root, d)).isDirectory());
const lines = tags.map((t) => `export * from "./${t}/${t}";`);
writeFileSync(join(root, "index.ts"), lines.join("\n") + "\n");
console.log(`endpoints barrel: ${tags.join(", ")}`);

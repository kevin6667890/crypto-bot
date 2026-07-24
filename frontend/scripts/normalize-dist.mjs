import { readFile, writeFile } from "node:fs/promises";

const path = new URL("../dist/index.html", import.meta.url);
const content = await readFile(path, "utf8");
await writeFile(path, content.replace(/\r\n?/g, "\n"), "utf8");

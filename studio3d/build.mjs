import { build } from "esbuild";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = (path) => fileURLToPath(new URL(path, import.meta.url));
const output = here("../office/static/office/studio/scene.js");
const result = await build({
  absWorkingDir: here("./"),
  entryPoints: ["src/scene.tsx"],
  outfile: output,
  bundle: true,
  minify: true,
  legalComments: "eof",
  target: ["es2022"],
  platform: "browser",
  format: "esm",
  jsx: "automatic",
  write: false,
  metafile: true,
  define: { "process.env.NODE_ENV": '"production"' },
  alias: { "@": here("./src/") },
});

const dependencyPaths = new Set();
for (const file of Object.keys(result.metafile.inputs)) {
  const packagePath = file.match(/^((?:.*\/)?node_modules\/(?:@[^/]+\/)?[^/]+)\//)?.[1];
  if (packagePath) dependencyPaths.add(packagePath);
}
const licenses = ["Nabio Office studio. Derived from Claw3D 51337f7f250459af61a7cf92b7b0015d59faed5c.",
  await readFile(here("./LICENSE-Claw3D"), "utf8")];
for (const path of [...dependencyPaths].sort()) {
  const directory = resolve(here("./"), path);
  const files = await readdir(directory);
  const metadata = JSON.parse(await readFile(resolve(directory, "package.json"), "utf8"));
  licenses.push(`${metadata.name} ${metadata.version} — ${metadata.license}`);
  for (const file of files.filter((name) => /^(licen[cs]e|notice)(\.|$)/i.test(name)).sort()) {
    licenses.push(await readFile(resolve(directory, file), "utf8"));
  }
}
const notice = `/*!\n${licenses.join("\n\n").replaceAll("*/", "* /")}\n*/\n`;
await mkdir(dirname(output), { recursive: true });
await writeFile(output, notice + result.outputFiles[0].text, "utf8");
console.log(`Studio bundle: ${Math.ceil(Buffer.byteLength(notice + result.outputFiles[0].text) / 1024)} KiB`);

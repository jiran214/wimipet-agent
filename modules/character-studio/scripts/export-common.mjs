import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import {
  parsePackage,
  fromPackage,
  confirmDraft,
  exportPackage,
  serializePackage,
} from "../src/core.mjs";
const source = await readFile(
  new URL("../fixtures/common-v1.character.json", import.meta.url),
  "utf8",
);
const imported = await parsePackage(source),
  project = { draft: fromPackage(imported), confirmed: imported };
await confirmDraft(project);
const pkg = await exportPackage(project);
const output = serializePackage(pkg);
await mkdir(new URL("../fixtures/exports/", import.meta.url), {
  recursive: true,
});
await writeFile(
  new URL(
    "../fixtures/exports/common-v1.export.character.json",
    import.meta.url,
  ),
  output,
);
const sha = (text) => createHash("sha256").update(text).digest("hex");
const receipt = {
  protocol: pkg.protocol,
  packageId: pkg.packageId,
  revision: pkg.revision,
  sourceCommit: "a24c406101262d35a53da7f1f7bd0d9639d19994",
  sourceSHA256: sha(source),
  exportSHA256: sha(output),
  exportBytes: Buffer.byteLength(output),
  actions: pkg.actions.map((a) => ({
    actionId: a.actionId,
    allowedStates: a.allowedStates,
    contactKind: a.contactKind,
  })),
  validation: "B public core real PNG decode and roundtrip; A consumer not run",
};
await writeFile(
  new URL("../fixtures/exports/common-v1.export.receipt.json", import.meta.url),
  JSON.stringify(receipt, null, 2) + "\n",
);
console.log(JSON.stringify(receipt, null, 2));

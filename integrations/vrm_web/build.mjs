import { build } from "esbuild";

await build({
  entryPoints: ["src/vrm_renderer.js"],
  bundle: true,
  minify: true,
  sourcemap: false,
  format: "esm",
  outfile: "../../companion_assets/vrm_renderer.mjs",
});

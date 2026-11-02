import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadConfigFromFile, type Plugin, type PluginOption } from "vite-plus";
import { checkLanding, type LandingCheckOptions } from "./check.js";

const usage = "Usage: laching-landing check [dist] [--config vite.config.ts]";

/** The check's options that `companionLanding`'s page plugin carries, found among a Vite config's plugins. */
export async function landingCheckOptions(
  plugins: PluginOption | undefined,
): Promise<LandingCheckOptions | undefined> {
  const plugin = await plugins;
  if (!plugin) return undefined;
  if (Array.isArray(plugin)) {
    for (const each of plugin) {
      const found = await landingCheckOptions(each);
      if (found) return found;
    }
    return undefined;
  }
  return "name" in plugin && plugin.name === "laching-landing-html"
    ? (plugin as Plugin).api?.check
    : undefined;
}

/**
 * `laching-landing check [dist] [--config file]`: loads the landing's Vite config from `cwd` (or `--config`), takes
 * `companionLanding`'s options from it and checks the built site (`dist`, the config's `build.outDir` by default).
 * Prints the problems and returns the exit code: 0 when it passes, 1 with problems, 2 on a usage error.
 */
export async function main(
  args: readonly string[],
  cwd = process.cwd(),
  print: (line: string) => void = console.log,
): Promise<number> {
  const [command, ...rest] = args;
  let configFile: string | undefined;
  const positional: string[] = [];
  let unknown = false;
  for (let index = 0; index < rest.length; index++) {
    const arg = rest[index]!;
    if (arg === "--config") configFile = rest[++index] ?? "";
    else if (arg.startsWith("--config=")) configFile = arg.slice("--config=".length);
    else if (arg.startsWith("-")) unknown = true;
    else positional.push(arg);
  }
  if (command !== "check" || unknown || positional.length > 1 || configFile === "") {
    print(usage);
    return 2;
  }
  let loaded: Awaited<ReturnType<typeof loadConfigFromFile>>;
  try {
    loaded = await loadConfigFromFile(
      { command: "build", mode: "production" },
      configFile && resolve(cwd, configFile),
      cwd,
      "silent",
    );
  } catch (error) {
    print(
      `Can't load ${configFile ?? "the Vite config"}: ${(error as Error).message.split("\n")[0]}`,
    );
    return 2;
  }
  if (!loaded) {
    print(`No Vite config in ${cwd}.`);
    return 2;
  }
  const options = await landingCheckOptions(loaded.config.plugins);
  if (!options) {
    print(`${loaded.path} has no companionLanding plugins.`);
    return 2;
  }
  const root = resolve(cwd, loaded.config.root ?? ".");
  const dist = positional[0]
    ? resolve(cwd, positional[0])
    : resolve(root, loaded.config.build?.outDir ?? "dist");
  if (!existsSync(dist)) {
    print(`No built site at ${dist}: build it first.`);
    return 2;
  }
  const problems = checkLanding(dist, options);
  for (const problem of problems) print(problem);
  print(
    problems.length
      ? `${problems.length} problem${problems.length === 1 ? "" : "s"} in ${dist}`
      : `${dist} passes`,
  );
  return problems.length ? 1 : 0;
}

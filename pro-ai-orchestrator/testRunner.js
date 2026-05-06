import { run } from "./runner.js";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { config } from "./config.js";

const DEFAULT_CLI_TESTS = config.project.testTargets;

function normalizePath(p) {
  return String(p || "").trim().replaceAll("\\", "/");
}

function hasNpmTestScript(dir) {
  const pkgPath = path.join(dir, "package.json");
  if (!existsSync(pkgPath)) return false;

  try {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
    return Boolean(pkg?.scripts?.test && String(pkg.scripts.test).trim());
  } catch {
    return false;
  }
}

function collectGitChangedFiles(repoRoot) {
  const changed = new Set();
  const commands = [
    ["diff", "--name-only"],
    ["diff", "--name-only", "--cached"],
    ["ls-files", "--others", "--exclude-standard"],
  ];

  for (const args of commands) {
    try {
      const out = execFileSync("git", args, {
        cwd: repoRoot,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      });

      for (const line of out.split(/\r?\n/)) {
        const rel = normalizePath(line);
        if (rel) changed.add(rel);
      }
    } catch {
      // Ignore when git metadata is unavailable.
    }
  }

  return [...changed];
}

function selectCliTests(cliDir, changedFiles, contextText) {
  const selected = new Set();
  const addIfExists = (relPath) => {
    if (existsSync(path.join(cliDir, relPath))) selected.add(relPath);
  };

  const cliChanges = changedFiles
    .filter((p) => p.startsWith("cli/"))
    .map((p) => p.slice("cli/".length));

  for (const rel of cliChanges) {
    if (rel.startsWith("tests/test_") && rel.endsWith(".py")) {
      addIfExists(rel);
    }
  }

  const signalText = `${cliChanges.join("\n")}\n${contextText}`.toLowerCase();

  for (const trigger of config.project.testTriggers) {
    if (trigger.regex.test(signalText)) {
      for (const test of trigger.tests) {
        addIfExists(test);
      }
    }
  }

  if (selected.size === 0 && (cliChanges.length > 0 || /(subtitle|quality|player|download)/.test(signalText))) {
    for (const rel of DEFAULT_CLI_TESTS) addIfExists(rel);
  }

  return [...selected];
}

function shouldRunBackendTests(changedFiles, contextText) {
  if (changedFiles.some((p) => p.startsWith("backend/"))) return true;
  return /(backend|sourcepipeline|provider|proxy|openapi|api\.js)/.test(contextText);
}

export async function runTests(context = {}) {
  const orchestratorDir = process.cwd();
  const repoRoot = path.resolve(orchestratorDir, "..");
  const contextText = `${context?.task || ""}\n${context?.code || ""}`.toLowerCase();
  const changedFiles = collectGitChangedFiles(repoRoot);
  const logs = [];

  try {
    if (hasNpmTestScript(orchestratorDir)) {
      const output = await run("npm", ["test"], {
        label: "🧪 Running Tests",
        cwd: orchestratorDir,
      });
      logs.push(`Orchestrator tests:\n${output}`);
      return { success: true, output: logs.join("\n\n"), skipped: false };
    }

    const cliDir = path.join(repoRoot, "cli");
    const pytestIni = path.join(cliDir, "pytest.ini");
    const backendDir = path.join(repoRoot, "backend");

    let ranAny = false;

    if (existsSync(pytestIni)) {
      const cliTests = selectCliTests(cliDir, changedFiles, contextText);
      if (cliTests.length > 0) {
        const testList = cliTests.join(", ");
        logs.push(`Selected CLI tests: ${testList}`);

        const pythonBin = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
        const venvPython = path.join(repoRoot, ".venv", pythonBin);
        const pythonCmd = existsSync(venvPython) ? venvPython : "python";
        const output = await run(
          pythonCmd,
          ["-m", "pytest", "-q", ...cliTests],
          {
            label: "🧪 Running CLI Tests",
            cwd: cliDir,
          }
        );
        logs.push(output);
        ranAny = true;
      }
    }

    if (hasNpmTestScript(backendDir) && shouldRunBackendTests(changedFiles, contextText)) {
      const output = await run("npm", ["test"], {
        label: "🧪 Running Backend Tests",
        cwd: backendDir,
      });
      logs.push(`Backend tests:\n${output}`);
      ranAny = true;
    }

    if (!ranAny && existsSync(pytestIni)) {
      logs.push("No high-signal targets detected; running focused CLI smoke tests.");
      const pythonBin = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
      const venvPython = path.join(repoRoot, ".venv", pythonBin);
      const pythonCmd = existsSync(venvPython) ? venvPython : "python";
      const output = await run(
        pythonCmd,
        ["-m", "pytest", "-q", ...DEFAULT_CLI_TESTS.filter((rel) => existsSync(path.join(cliDir, rel)))],
        {
          label: "🧪 Running CLI Tests",
          cwd: cliDir,
        }
      );
      logs.push(output);
      ranAny = true;
    }

    if (!ranAny) {
      const output = "No matching test targets found for current task/changes. Skipping tests.";
      return { success: true, output, skipped: true };
    }

    return { success: true, output: logs.join("\n\n"), skipped: false };
  } catch (err) {
    return { success: false, output: err, skipped: false };
  }
}

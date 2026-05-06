#!/usr/bin/env node
import inquirer from "inquirer";
import chalk from "chalk";
import boxen from "boxen";
import { readFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { runPipeline } from "./pipeline.js";

function printHeader() {
  console.log(
    boxen(chalk.bold.cyan("🚀 PRO AI ORCHESTRATOR"), {
      padding: 1,
      margin: 1,
      borderStyle: "double",
      borderColor: "cyan",
    })
  );
}

function exitWithArgError(message) {
  console.error(chalk.red(message));
  process.exit(1);
}

function readNextArgValue(args, index, argName) {
  const value = args[index + 1];
  if (!value || value.startsWith("-")) {
    exitWithArgError(`Error: Missing value for ${argName}`);
  }
  return value;
}

function parseCliArgs(argv) {
  const args = argv.slice(2);
  const result = {
    prompt: null,
    promptFile: null,
    paste: false,
    maxRounds: null,
    autoApprove: false,
    help: false
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];

    switch (arg) {
      case "-h":
      case "--help":
        result.help = true;
        break;
      case "-y":
      case "--yes":
        result.autoApprove = true;
        break;
      case "-p":
      case "--prompt": {
        const value = args[i + 1];
        if (!value || value.startsWith("-")) {
          // Allow -p/--prompt without a value to enter direct paste mode.
          result.paste = true;
          break;
        }
        result.prompt = value;
        i += 1;
        break;
      }
      case "--paste":
        result.paste = true;
        break;
      case "--prompt-file": {
        const value = readNextArgValue(args, i, arg);
        result.promptFile = value;
        i += 1;
        break;
      }
      case "-m":
      case "--max-rounds": {
        const value = readNextArgValue(args, i, arg);
        const parsed = Number.parseInt(value, 10);
        if (Number.isNaN(parsed)) {
          exitWithArgError(`Error: Invalid value for ${arg}`);
        }
        result.maxRounds = parsed;
        i += 1;
        break;
      }
      default:
        break;
    }
  }

  return result;
}

function printHelp() {
  console.log(`
${chalk.bold("Usage:")}
    ai-orc -p "your task"
    ai-orc --paste
    ai-orc --prompt-file ./task.txt
    echo "your task" | ai-orc
    ai-orc -m 5 -p "complex task"

${chalk.bold("Options:")}
  -p, --prompt <text>     Task description (or use -p alone for paste mode)
  --paste                 Paste multiline prompt directly in terminal
  --prompt-file <path>    Load task from file
  -m, --max-rounds <num>  Maximum fix iterations (default: 3)
  -h, --help              Show this help
`);
}

async function askTaskFromPaste() {
  console.log(
    chalk.cyan(
      "\nPaste your prompt below. End input with a single line containing /end"
    )
  );

  const rl = createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: true,
  });

  const lines = [];
  for await (const line of rl) {
    if (line.trim() === "/end") {
      rl.close();
      break;
    }
    lines.push(line);
  }

  const task = lines.join("\n").trim();
  if (!task) {
    throw new Error("Pasted task cannot be empty.");
  }
  return task;
}

async function readTaskFromStdin() {
  if (process.stdin.isTTY) return null;

  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  const text = Buffer.concat(chunks).toString("utf8").trim();
  return text || null;
}

async function askTaskInteractively() {
  while (true) {
    const { mode } = await inquirer.prompt([
      {
        type: "list",
        name: "mode",
        message: chalk.cyan("How would you like to provide your task?"),
        choices: [
          { name: "Quick single-line prompt", value: "quick" },
          { name: "Paste multiline prompt directly", value: "paste" },
          { name: "Open external editor", value: "editor" },
          { name: "Load prompt from file", value: "file" },
        ],
      },
    ]);

    if (mode === "quick") {
      const { task } = await inquirer.prompt([
        {
          type: "input",
          name: "task",
          message: chalk.cyan("What would you like to build today?"),
          validate: (value) => (value?.trim() ? true : "Task cannot be empty"),
        },
      ]);
      return task.trim();
    }

    if (mode === "paste") {
      return askTaskFromPaste();
    }

    if (mode === "editor") {
      const { editorTask } = await inquirer.prompt([
        {
          type: "editor",
          name: "editorTask",
          message: "Write your full task in the editor and save/close:",
          validate: (value) => (value?.trim() ? true : "Task cannot be empty"),
        },
      ]);
      return editorTask.trim();
    }

    const { filePath } = await inquirer.prompt([
      {
        type: "input",
        name: "filePath",
        message: "Prompt file path:",
        validate: (value) => (value?.trim() ? true : "Path cannot be empty"),
      },
    ]);

    try {
      const content = await readFile(filePath.trim(), "utf8");
      const task = content.trim();
      if (!task) {
        console.log(chalk.yellow("Prompt file is empty. Please try again."));
        continue;
      }
      return task;
    } catch (error) {
      console.log(chalk.red(`Could not read file: ${error.message}`));
    }
  }
}

async function resolveTask(cli) {
  if (cli.help) {
    printHelp();
    process.exit(0);
  }

  if (cli.promptFile) {
    const content = await readFile(cli.promptFile, "utf8");
    return content.trim();
  }

  if (cli.prompt) {
    return cli.prompt.trim();
  }

  const pipedTask = await readTaskFromStdin();
  if (pipedTask) {
    return pipedTask;
  }

  if (cli.paste) {
    return askTaskFromPaste();
  }

  return askTaskInteractively();
}

async function main() {
  printHeader();
  const cli = parseCliArgs(process.argv);
  const task = await resolveTask(cli);

  console.log(chalk.blue(`\n⚡ Initializing pipeline for: ${chalk.italic(task.substring(0, 50))}${task.length > 50 ? '...' : ''}\n`));

  const result = await runPipeline(task, {
    maxRounds: cli.maxRounds,
    autoApprove: cli.autoApprove
  });

  if (result.status === 'success' || result.status === 'max_rounds') {
    console.log(boxen(chalk.green.bold("🎯 FINAL OUTPUT"), { padding: 1, borderColor: 'green', margin: 1 }));
    console.log(result.code);
  }
}

try {
  await main();
} catch (error) {
  console.error(chalk.red("\n❌ Pipeline failed\n"));
  console.error(error);
  process.exit(1);
}

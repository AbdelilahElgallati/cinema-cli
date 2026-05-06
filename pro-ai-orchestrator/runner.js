import { execa } from 'execa';
import chalk from 'chalk';
import ora from 'ora';

function sanitizeOutput(raw = "") {
  if (typeof raw !== "string") {
    raw = String(raw || "");
  }
  return raw
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed) return true;
      if (trimmed.startsWith('YOLO mode is enabled.')) return false;
      if (trimmed.includes('Tool with name "mcp_github_') && trimmed.includes('already registered. Overwriting.')) return false;
      if (trimmed.includes('conpty_console_list_agent.js')) return false;
      return true;
    })
    .join('\n')
    .replaceAll(/\n{3,}/g, '\n\n')
    .trim();
}

export async function run(cmd, args = [], options = {}, stdinInput = null) {
  const { 
    label = 'Executing', 
    silent = false,
    cwd = process.cwd()
  } = options;

  let spinner;
  if (!silent) {
    spinner = ora({
      text: chalk.blue(`${label}...`),
      color: 'blue'
    }).start();
  }

  try {
    const subprocess = execa(cmd, args, {
      all: true,
      input: stdinInput ?? undefined,
      cwd,
      env: { 
        ...process.env, 
        FORCE_COLOR: 'true',
        PTY_PREFER_CONPTY: '0', // Disable ConPTY to avoid AttachConsole issues on some Win32 environments
        IS_TERMINAL: 'false'
      }
    });

    let lastOutput = "";
    let updateInterval;

    if (!silent && spinner) {
      subprocess.all.on('data', (data) => {
        lastOutput = data.toString().trim().split('\n').pop() || lastOutput;
      });

      updateInterval = setInterval(() => {
        if (lastOutput && lastOutput.length < 80) {
          spinner.text = chalk.blue(`${label}: `) + chalk.gray(lastOutput);
        }
      }, 100);
    }

    const { all } = await subprocess;
    if (updateInterval) clearInterval(updateInterval);
    
    if (!silent && spinner) {
      spinner.succeed(chalk.green(`${label} completed.`));
    }
    
    return sanitizeOutput(all);
  } catch (error) {
    if (!silent && spinner) {
      spinner.fail(chalk.red(`${label} failed.`));
    }
    const raw = error.all || error.message || String(error);
    throw sanitizeOutput(raw);
  }
}
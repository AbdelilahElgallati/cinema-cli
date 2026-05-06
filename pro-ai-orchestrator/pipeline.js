import { plan, review } from "./agents/gemini.js";
import { build } from "./agents/copilot.js";
import { runTests } from "./testRunner.js";
import inquirer from "inquirer";
import chalk from "chalk";
import boxen from "boxen";
import { config } from "./config.js";

async function approveStep(stepName, content, options = {}) {
  if (options.autoApprove || config.orchestrator.autoApprove) return true;
  console.log(boxen(content, { title: stepName, padding: 1, borderColor: 'cyan', margin: 1 }));
  const { approved } = await inquirer.prompt([
    {
      type: "confirm",
      name: "approved",
      message: `Do you approve this ${stepName}?`,
      default: true
    }
  ]);
  return approved;
}

function needsFix(reviewText, testResult) {
  if (testResult.success === false) return true;
  
  const cleanReview = reviewText.trim();
  if (cleanReview.startsWith('PASSED')) return false;

  const bugSignal = /(?:^|\n)(?:\[BUG\]|\[ISSUE\]|\[ERROR\]|ISSUE:|BUG:|ERROR:)/i;
  return bugSignal.test(cleanReview);
}

function isCrashLog(text) {
  const crashPatterns = [
    /Error: AttachConsole failed/i,
    /at Module\._compile/i,
    /node:internal\/modules\/cjs\/loader/i,
    /Error: Cannot find module/i,
    /at (?:async )?.* \(node:internal/i,
    /^\s*at .* \((?:node:)?internal\/.*\)/m,
    /^var consoleProcessList = getConsoleProcessList/m
  ];
  return crashPatterns.some(pattern => pattern.test(text));
}

export async function runPipeline(task, options = {}) {
  const maxRounds = options.maxRounds ?? config.orchestrator.maxRounds;

  // Step 1: Planning
  const planResult = await plan(task);
  const planApproved = await approveStep("Proposed Plan", planResult, options);
  if (!planApproved) {
    console.log(chalk.yellow("Pipeline stopped by user."));
    return { status: 'stopped' };
  }

  // Step 2: Building
  let code;
  try {
    code = await build(planResult);
    if (isCrashLog(code)) {
      throw new Error("Build failed with a runtime crash log:\n" + code);
    }
  } catch (err) {
    console.error(chalk.red("\n❌ Initial build failed."));
    console.error(err);
    return { status: 'failed', error: err };
  }
  
  for (let i = 0; i < maxRounds; i++) {
    console.log(chalk.bold.blue(`\n🔄 ITERATION ROUND ${i + 1}\n`));

    // Step 3: Testing
    const testResult = await runTests({ task, code, round: i + 1 });
    if (!testResult.success) {
        console.log(chalk.red("❌ Tests failed."));
    } else if (testResult.skipped) {
      console.log(chalk.yellow("⚠️ Tests skipped."));
    } else {
        console.log(chalk.green("✅ Tests passed."));
    }

    // Step 4: Reviewing
    const reviewResult = await review(code);
    
    if (!needsFix(reviewResult, testResult)) {
      console.log(chalk.green.bold("\n✨ All checks passed! Final output is ready.\n"));
      return { planResult, code, reviewResult, status: 'success' };
    }

    console.log(chalk.yellow("\n⚠️ Issues found in review or tests."));
    const fixApproved = await approveStep("Review & Test Failures", `REVIEW:\n${reviewResult}\n\nTEST OUTPUT:\n${testResult.output}`, options);
    
    if (!fixApproved) {
        console.log(chalk.yellow("Pipeline stopped by user."));
        return { status: 'stopped', code };
    }

    console.log(chalk.blue("🔧 Fixing code..."));
    const fixInput = `
Fix this code based on review and test results:

CODE:
${code}

TEST RESULT:
${testResult.output}

REVIEW:
${reviewResult}
    `;

    try {
      code = await build(fixInput);
      if (isCrashLog(code)) {
        throw new Error("Fix attempt failed with a runtime crash log:\n" + code);
      }
    } catch (err) {
      console.error(chalk.red(`\n❌ Fix attempt in round ${i + 1} failed.`));
      console.error(err);
      return { status: 'failed', error: err, code };
    }
  }

  console.log(chalk.red("\n⚠️ Max iterations reached without resolving all issues."));
  return { planResult, code, status: 'max_rounds' };
}

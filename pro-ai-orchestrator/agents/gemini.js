import { run } from "../runner.js";
import { config } from "../config.js";

export async function plan(task) {
  return run(config.planner.cmd, config.planner.args(), { label: '🧠 Planning' }, task);
}

export async function review(code) {
  const prompt = config.reviewer.prompt(code);
  return run(config.reviewer.cmd, config.reviewer.args(), { label: '🔍 Reviewing Code' }, prompt);
}
import { run } from "../runner.js";
import { config } from "../config.js";
 
export async function build(input) {
  return run(config.builder.cmd, config.builder.args(), { label: '🔨 Building Code' }, input);
}
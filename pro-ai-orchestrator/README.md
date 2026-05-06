# Pro AI Orchestrator

A professional, transparent, and interactive CLI for managing AI-driven coding tasks.

## Features

- **Professional UI**: Rich status indicators using spinners and boxes.
- **Full Transparency**: See exactly what the pipeline is doing at every step.
- **Approval Workflow**: Approve plans, code, and fixes before they are executed.
- **Iterative Fixing**: Automatically detects issues in tests or reviews and proposes fixes.
- **Flexible Input**: Supports command-line prompts, files, and an interactive editor.

## Usage

### Global Command Setup (One-Time)
```bash
cd pro-ai-orchestrator
npm link
```

After that, you can launch from any terminal with:
```bash
ai-orc
```

### Interactive Mode
```bash
node index.js
```

### Direct Prompt
```bash
node index.js -p "Create a simple express server"
```

### Prompt from File
```bash
node index.js --prompt-file ./task.txt
```

### Paste Multiline Prompt Directly
```bash
ai-orc --paste
```
Paste your prompt, then finish with a line containing `/end`.

### Pipe Prompt From Terminal
```bash
echo "Fix subtitle selection logic" | ai-orc
```

## Pipeline Steps

1. **🧠 Planning**: Gemini proposes a plan for your task.
2. **🔨 Building**: Copilot generates the code based on the plan.
3. **🧪 Testing**: Runs `npm test` to validate the code.
4. **🔍 Reviewing**: Gemini reviews the code for potential bugs or improvements.
5. **🔧 Fixing**: If tests fail or review finds issues, the AI proposes a fix.

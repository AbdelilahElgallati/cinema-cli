export const config = {
  orchestrator: {
    maxRounds: 3,
    autoApprove: false,
  },

  planner: {
    cmd: "gemini",
    args: () => ["-p", "You are an expert software architect. Create a detailed, step-by-step technical plan for the given task. Use stdin for full instructions. If you agree with the current state, say 'NO_CHANGES_NEEDED'.", "--yolo", "--output-format", "text"]
  },
 
  builder: {
    cmd: "gemini",
    args: () => ["-p", "You are a senior software engineer. Implement the requested changes following best practices, strict typing, and project conventions. Use stdin for full instructions.", "--yolo", "--output-format", "text"]
  },
 
  reviewer: {
    cmd: "gemini",
    args: () => ["-p", "You are a rigorous code reviewer. Analyze the code for bugs, logic errors, security risks, and performance issues. Use stdin for full instructions. If the code is perfect, start your response with 'PASSED'. Otherwise, use tags like [BUG], [ISSUE], or [ERROR].", "--yolo", "--output-format", "text"],
    prompt: (code) => `Review this code for bugs, logic errors, and improvements. Be specific with file names and line numbers:\n\n${code}`
  },
 
  project: {
    name: "cinema-cli",
    testTargets: [
      "tests/test_subtitle_fallback.py",
      "tests/test_player_ipc.py",
      "tests/test_quality.py",
    ],
    testTriggers: [
      {
        regex: /subtitle|subtitles|opensubtitles|subdl|fallback|download_manager/i,
        tests: ["tests/test_subtitle_fallback.py"]
      },
      {
        regex: /player|mpv|ipc|play_stream/i,
        tests: ["tests/test_player_ipc.py"]
      },
      {
        regex: /quality|source_strategy|ytdl|resolution/i,
        tests: ["tests/test_quality.py"]
      }
    ]
  }
};
 
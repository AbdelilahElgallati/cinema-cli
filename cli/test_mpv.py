import subprocess


def run_probe(arg):
    args = ["C:\\ProgramData\\chocolatey\\lib\\mpvio.install\\tools\\mpv.COM", arg, "--idle"]
    proc = subprocess.run(args, capture_output=True, text=True)
    print(f"ARG: {arg}")
    print("STDOUT:", proc.stdout.strip())
    print("STDERR:", proc.stderr.strip())
    print("-" * 40)


if __name__ == "__main__":
    run_probe(r"--ytdl-raw-options=format-sort=res\,fps")
    run_probe('--ytdl-raw-options=format-sort="res,fps"')
    run_probe("--ytdl-raw-options=format-sort='res,fps'")
    run_probe("--ytdl-raw-options=format-sort=res%2Cfps")
    run_probe("--ytdl-raw-options-append=format-sort=res,fps")

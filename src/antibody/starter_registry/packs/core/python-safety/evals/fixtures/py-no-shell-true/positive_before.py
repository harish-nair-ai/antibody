import subprocess


def git_status():
    return subprocess.run(["git", "status"], check=True, capture_output=True, text=True)

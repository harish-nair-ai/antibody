import subprocess


def git_status():
    return subprocess.run("git status", shell=True, check=True, capture_output=True, text=True)

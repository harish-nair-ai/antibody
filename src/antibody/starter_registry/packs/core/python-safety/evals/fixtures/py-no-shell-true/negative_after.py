import subprocess


def git_status():
    return subprocess.run(["git", "status", "--short"], check=True, shell=False, capture_output=True, text=True)

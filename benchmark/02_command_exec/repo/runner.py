"""A small job-runner module with two real problems planted in it."""

import os
import pickle
import subprocess


def run_job(name, args):
    # Problem: command built from user input and run through the shell.
    cmd = f"/opt/jobs/{name} {args}"
    return subprocess.run(cmd, shell=True, capture_output=True)


def load_state(blob):
    # Problem: pickle.loads on untrusted bytes is arbitrary code execution.
    return pickle.loads(blob)


def cleanup(path):
    # Problem: unsanitised path handed to a shell rm.
    os.system("rm -rf " + path)

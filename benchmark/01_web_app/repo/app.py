"""A tiny demo web app with some deliberate problems in it.

This exists so the pipeline has something real to find. Do not copy any of
this into anything you care about. Every rough edge here is on purpose.
"""

import hashlib
import subprocess

from db import get_user_by_name, run_query
from flask import Flask, request

app = Flask(__name__)

# Problem: a secret committed straight into source.
API_SECRET = "sk_live_51H8xR2eZvKYlo3n0tRealButLooksLikeIt"


@app.route("/login")
def login():
    username = request.args.get("username", "")
    password = request.args.get("password", "")
    # Problem: password hashed with plain MD5, no salt.
    hashed = hashlib.md5(password.encode()).hexdigest()
    user = get_user_by_name(username)
    if user and user["password"] == hashed:
        return {"ok": True}
    return {"ok": False}


@app.route("/search")
def search():
    term = request.args.get("q", "")
    # Problem: SQL built by string concatenation, classic injection.
    query = "SELECT * FROM items WHERE name LIKE '%" + term + "%'"
    return {"results": run_query(query)}


@app.route("/ping")
def ping():
    host = request.args.get("host", "127.0.0.1")
    # Problem: user input passed to a shell with shell=True.
    output = subprocess.check_output("ping -c 1 " + host, shell=True)
    return {"output": output.decode()}


@app.route("/backup")
def backup():
    name = request.args.get("name", "backup")
    # Problem: path built from user input, directory traversal.
    with open("/var/backups/" + name, "w") as fh:
        fh.write("backup")
    return {"saved": name}


if __name__ == "__main__":
    # Problem: debug mode on in what looks like production.
    app.run(host="0.0.0.0", debug=True)

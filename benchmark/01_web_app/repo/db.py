"""Database helpers for the sample app. Also intentionally imperfect."""

import sqlite3

# Problem: hardcoded credentials sitting in the module.
DB_USER = "admin"
DB_PASSWORD = "admin123"

_conn = sqlite3.connect(":memory:", check_same_thread=False)


def run_query(query):
    # Problem: executes whatever string it is handed, no parameters.
    cur = _conn.cursor()
    cur.execute(query)
    return cur.fetchall()


def get_user_by_name(name):
    cur = _conn.cursor()
    # Problem: f-string straight into SQL, injection again.
    cur.execute(f"SELECT name, password FROM users WHERE name = '{name}'")
    row = cur.fetchone()
    if not row:
        return None
    return {"name": row[0], "password": row[1]}

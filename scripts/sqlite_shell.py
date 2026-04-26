import sqlite3
import os
from db.db import _try_load_sqlite_vec, DEFAULT_DB_PATH

def run_shell():
    db_path = DEFAULT_DB_PATH
    conn = sqlite3.connect(db_path)
    
    _try_load_sqlite_vec(conn)
    
    print(f"Connected to SQLite database: {db_path}")
    print("Type '.help' for usage hints.")
    print("Type '.tables' to list tables.")
    print("Type '.schema <table_name>' to see table schema.")
    
    # Enter interactive shell
    try:
        conn.isolation_level = None  # Autocommit mode
        cursor = conn.cursor()
        while True:
            try:
                command = input("sqlite> ")
                if command.strip().lower() == ".quit":
                    break
                elif command.strip().lower() == ".tables":
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                    tables = cursor.fetchall()
                    for table in tables:
                        print(table[0])
                elif command.strip().lower().startswith(".schema"):
                    parts = command.split()
                    if len(parts) > 1:
                        table_name = parts[1]
                        cursor.execute(f"PRAGMA table_info('{table_name}');")
                        schema = cursor.fetchall()
                        for col in schema:
                            print(f"  {col[1]} ({col[2]}) - {'PK' if col[5] else ''}")
                    else:
                        print("Usage: .schema <table_name>")
                else:
                    cursor.execute(command)
                    results = cursor.fetchall()
                    for row in results:
                        print(row)
            except sqlite3.Error as e:
                print(f"Error: {e}")
            except EOFError: # Ctrl+D
                break
    finally:
        conn.close()

if __name__ == "__main__":
    run_shell()

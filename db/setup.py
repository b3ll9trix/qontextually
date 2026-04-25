import sqlite3
import os
import glob
import argparse

def initialize_and_migrate(db_path: str, migrations_dir: str):
    # Ensure the parent directory for the database exists
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    
    print(f"🗄️ Connecting to {db_path}...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Create the internal tracking table for migrations
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()

    # 2. Get the list of already applied migrations
    cursor.execute('SELECT version FROM _schema_migrations')
    applied_migrations = {row[0] for row in cursor.fetchall()}

    # 3. Find all .sql files in the migrations directory
    if not os.path.exists(migrations_dir):
        print(f"❌ Error: Directory '{migrations_dir}' not found.")
        conn.close()
        return

    migration_files = sorted(glob.glob(os.path.join(migrations_dir, "*.sql")))

    if not migration_files:
        print(f"⚠️ No .sql files found in {migrations_dir}/.")
        conn.close()
        return

    # 4. Apply new migrations in order
    changes_made = False
    for file_path in migration_files:
        filename = os.path.basename(file_path)
        
        if filename not in applied_migrations:
            print(f"⏳ Applying new migration: {filename}...")
            
            with open(file_path, 'r', encoding='utf-8') as file:
                sql_script = file.read()
            
            try:
                cursor.executescript(sql_script)
                cursor.execute('INSERT INTO _schema_migrations (version) VALUES (?)', (filename,))
                conn.commit()
                changes_made = True
                print(f"  ✅ Successfully applied {filename}")
                
            except sqlite3.Error as e:
                print(f"  ❌ ERROR applying {filename}: {e}")
                conn.rollback()
                print("🛑 Migration sequence halted to protect database integrity.")
                break 
        else:
            print(f"⏭️ Skipping {filename} (already applied).")

    if not changes_made:
        print("✨ Database schema is already fully up to date.")
        
    conn.close()
    print("🏁 Database setup complete.")

if __name__ == "__main__":
    # Set up command-line arguments so the script is completely independent
    parser = argparse.ArgumentParser(description="Generic SQLite Migration Engine")
    parser.add_argument("--db", required=True, help="Path to the SQLite database file")
    parser.add_argument("--migrations", required=True, help="Path to the directory containing .sql files")
    
    args = parser.parse_args()
    
    initialize_and_migrate(db_path=args.db, migrations_dir=args.migrations)

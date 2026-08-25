import sqlite3

db_path = "sneakers.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE notification ADD COLUMN user_id INTEGER")
    print("✅ user_id column added successfully.")
except Exception as e:
    print("⚠️ Column may already exist:", e)

conn.commit()
conn.close()
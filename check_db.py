import sqlite3
import os

def check_database_schema(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        print(f"Tables in {db_path}:")
        for table in tables:
            print(f"\nTable: {table[0]}")
            cursor.execute(f"PRAGMA table_info({table[0]});")
            columns = cursor.fetchall()
            for col in columns:
                print(f"  Column: {col[1]} ({col[2]})")
        
        conn.close()
    except Exception as e:
        print(f"Error checking database: {str(e)}")

if __name__ == "__main__":
    db_path = os.path.join("instance", "chatbot.db")
    check_database_schema(db_path) 
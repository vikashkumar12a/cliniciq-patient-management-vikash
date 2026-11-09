import sqlite3
from pathlib import Path
from getpass import getpass
from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "database.db"


username = input("Enter admin username: ").strip()
password = getpass("Enter admin password: ")
confirm_password = getpass("Confirm admin password: ")

if not username or not password:
    print("Username and password cannot be empty.")

elif password != confirm_password:
    print("Passwords do not match.")

elif len(password) < 8:
    print("Password must be at least 8 characters.")

else:
    conn = sqlite3.connect(DATABASE)

    try:
        password_hash = generate_password_hash(password)

        conn.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            (username, password_hash)
        )

        conn.commit()
        print("Admin account created successfully!")

    except sqlite3.IntegrityError:
        print("This username already exists.")

    finally:
        conn.close()
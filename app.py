from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import os
import shutil
import csv
from io import StringIO, BytesIO
from flask import send_file

app = Flask(__name__)

app.secret_key = os.environ.get("CLINICIQ_SECRET_KEY") or os.urandom(32)

# Session Security Settings
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "database.db"


# Database Connection
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# Database Backup Function
def create_database_backup():

    backup_folder = BASE_DIR / "backups"
    backup_folder.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    backup_path = backup_folder / f"clinic_backup_{timestamp}.db"

    source_conn = sqlite3.connect(DATABASE)
    backup_conn = sqlite3.connect(backup_path)

    try:
        source_conn.backup(backup_conn)

    finally:
        source_conn.close()
        backup_conn.close()

    return backup_path
# Admin Login Required
def login_required(route_function):

    @wraps(route_function)
    def wrapper(*args, **kwargs):

        if "admin_id" not in session:
            return redirect(url_for("login"))

        return route_function(*args, **kwargs)

    return wrapper

# Create Database Tables
def create_tables():

    conn = get_db_connection()

    # Patients Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            age INTEGER NOT NULL,
            gender TEXT NOT NULL,
            address TEXT,
            phone TEXT
        )
    """)

    # Patient Visits Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            patient_type TEXT NOT NULL,
            problem TEXT,
            treatment TEXT,
            visit_date TEXT NOT NULL,
            doctor_fees REAL DEFAULT 0,
            medicine_charges REAL DEFAULT 0,

            FOREIGN KEY (patient_id)
            REFERENCES patients(id)
        )
    """)
        # Add visit_time column if it does not exist
    columns = conn.execute("PRAGMA table_info(visits)").fetchall()

    column_names = [column["name"] for column in columns]

    if "visit_time" not in column_names:
        conn.execute("""
            ALTER TABLE visits
            ADD COLUMN visit_time TEXT
        """)

        # Admin Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
# Check Admin Session Inactivity
@app.before_request
def check_session_timeout():

    if "admin_id" in session:

        current_time = datetime.now().timestamp()

        last_activity = session.get("last_activity", current_time)

        # Check 30 Minutes Inactivity
        if current_time - last_activity > 30 * 60:

            session.clear()

            return redirect(url_for("login"))

        # Update Last Activity Time
        session["last_activity"] = current_time

# Admin Login
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db_connection()

        admin = conn.execute(
            "SELECT * FROM admins WHERE username = ?",
            (username,)
        ).fetchone()

        conn.close()

        if admin and check_password_hash(admin["password_hash"], password):

            session.clear()
            session.permanent = True

            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            session["last_activity"] = datetime.now().timestamp()
            return redirect(url_for("dashboard"))

        return render_template(
            "login.html",
            error="Invalid username or password."
        )

    return render_template("login.html")


# Admin Logout
@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))
# Database Backup Route
@app.route("/backup")
@login_required

def backup_database():

    backup_path = create_database_backup()

    return f"Database backup created successfully: {backup_path.name}"

# Download Database Backup
@app.route("/download-backup")
@login_required
def download_backup():

    backup_path = create_database_backup()

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=backup_path.name
    )

# Database Restore Route
@app.route("/restore", methods=["GET", "POST"])
@login_required
def restore_database():

    backup_folder = BASE_DIR / "backups"

    backups = sorted(
        [file.name for file in backup_folder.glob("*.db")],
        reverse=True
    )

    if request.method == "POST":

        backup_file = request.form.get("backup_file", "")

        # Validate Backup File
        if not backup_file or Path(backup_file).name != backup_file:
            return render_template(
                "restore.html",
                backups=backups,
                error="Invalid backup file."
            )

        selected_backup = backup_folder / backup_file

        if not selected_backup.is_file():
            return render_template(
                "restore.html",
                backups=backups,
                error="Backup file not found."
            )
        if selected_backup.suffix.lower() != ".db":
            return render_template(
                "restore.html",
                backups=backups,
                error="Only database backup files are allowed."
            )

        try:
            # Check Database Integrity
            check_conn = sqlite3.connect(
                f"file:{selected_backup.as_posix()}?mode=ro",
                uri=True
            )

            integrity = check_conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            tables = {
                row[0] for row in check_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

            check_conn.close()

            required_tables = {"patients", "visits", "admins"}

            if integrity != "ok" or not required_tables.issubset(tables):
                raise ValueError("Backup database is invalid.")

            # Create Safety Backup
            create_database_backup()

            # Restore Database
            temp_path = DATABASE.with_name("database_restore.tmp")

            shutil.copy2(selected_backup, temp_path)
            os.replace(temp_path, DATABASE)

            return render_template(
                "restore.html",
                backups=backups,
                success="Database restored successfully!"
            )

        except Exception:
            if 'temp_path' in locals() and temp_path.exists():
                temp_path.unlink()

            return render_template(
                "restore.html",
                backups=backups,
                error="Restore failed. Original database was not intentionally changed."
            )

    return render_template(
        "restore.html",
        backups=backups
    )

# Download Patient Records (CSV)
@app.route("/download-patients")
@login_required
def download_patients():

    conn = get_db_connection()

    try:
        records = conn.execute("""
            SELECT
                p.id,
                p.name,
                p.age,
                p.gender,
                p.address,
                p.phone,
                v.patient_type,
                v.problem,
                v.treatment,
                v.visit_date,
                v.visit_time,
                v.doctor_fees,
                v.medicine_charges
            FROM patients p
            LEFT JOIN visits v ON p.id = v.patient_id
            ORDER BY p.id, v.visit_date
        """).fetchall()

    finally:
        conn.close()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Patient ID",
        "Name",
        "Age",
        "Gender",
        "Address",
        "Phone",
        "Patient Type",
        "Problem",
        "Treatment",
        "Visit Date",
        "Visit Time",
        "Doctor Fees",
        "Medicine Charges"
    ])

    for row in records:
        writer.writerow(list(row))

    csv_data = BytesIO(output.getvalue().encode("utf-8-sig"))

    return send_file(
        csv_data,
        mimetype="text/csv",
        as_attachment=True,
        download_name="ClinicIQ_Patient_Records.csv"
    )

@app.route("/")
def home():
    return redirect(url_for("dashboard"))

# Dashboard
@app.route("/dashboard")
@login_required
def dashboard():

    conn = get_db_connection()

    # Total Patients
    total_patients = conn.execute("""
        SELECT COUNT(*) FROM patients
    """).fetchone()[0]

    # Total Visits
    total_visits = conn.execute("""
        SELECT COUNT(*) FROM visits
    """).fetchone()[0]

    # Today's Patients
    today_patients = conn.execute("""
        SELECT COUNT(DISTINCT patient_id)
        FROM visits
        WHERE visit_date = DATE('now', 'localtime')
    """).fetchone()[0]

    # This Week's Patients
    weekly_patients = conn.execute("""
        SELECT COUNT(DISTINCT patient_id)
        FROM visits
        WHERE visit_date >= DATE(
            'now', 'localtime',
            'weekday 0', '-6 days'
        )
        AND visit_date <= DATE('now', 'localtime')
    """).fetchone()[0]

    # This Month's Patients
    monthly_patients = conn.execute("""
        SELECT COUNT(DISTINCT patient_id)
        FROM visits
        WHERE visit_date >= DATE(
            'now', 'localtime',
            'start of month'
        )
        AND visit_date <= DATE('now', 'localtime')
    """).fetchone()[0]

    # Patient Type Count
    patient_types = conn.execute("""
        SELECT patient_type, COUNT(*) AS total
        FROM visits
        GROUP BY patient_type
    """).fetchall()

    # Collection Details
    collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges
        FROM visits
    """).fetchone()

    # Daily Collection
    daily_collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges
        FROM visits
        WHERE visit_date = DATE('now', 'localtime')
    """).fetchone()

    # Weekly Collection
    weekly_collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges
        FROM visits
        WHERE visit_date >= DATE(
            'now', 'localtime',
            '-' || (
                (CAST(STRFTIME('%w', 'now', 'localtime') AS INTEGER) + 6) % 7
            ) || ' days'
        )
        AND visit_date <= DATE('now', 'localtime')
    """).fetchone()

    # Monthly Collection
    monthly_collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges
        FROM visits
        WHERE visit_date >= DATE(
            'now', 'localtime', 'start of month'
        )
        AND visit_date <= DATE('now', 'localtime')
    """).fetchone()

    # Date-wise Collection
    selected_date = request.args.get("report_date")

    if not selected_date:
        selected_date = datetime.now().strftime("%Y-%m-%d")

    date_collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges,
            COUNT(*) AS total_visits
        FROM visits
        WHERE visit_date = ?
    """, (selected_date,)).fetchone()

    # Last 7 Days Patient Visits Graph
    weekly_visit_data = conn.execute("""
        SELECT
            visit_date,
            COUNT(*) AS total
        FROM visits
        WHERE visit_date >= DATE('now', 'localtime', '-6 days')
        AND visit_date <= DATE('now', 'localtime')
        GROUP BY visit_date
    """).fetchall()

    visit_counts = {
        row["visit_date"]: row["total"]
        for row in weekly_visit_data
    }

    today = datetime.now().date()

    visit_chart_labels = [
        (today - timedelta(days=i)).strftime("%d %b")
        for i in range(6, -1, -1)
    ]

    visit_chart_values = [
        visit_counts.get(
            (today - timedelta(days=i)).strftime("%Y-%m-%d"),
            0
        )
        for i in range(6, -1, -1)
    ]

    # Patient Categories Graph
    patient_type_labels = [
        item["patient_type"] for item in patient_types
    ]

    patient_type_values = [
        item["total"] for item in patient_types
    ]

    # Collection Comparison Graph
    collection_chart_labels = ["Doctor Fees", "Medicine Charges"]
    collection_chart_values = [
        collection["doctor_fees"],
        collection["medicine_charges"]
    ]

    conn.close()

    # Total Collection
    total_collection = (
        collection["doctor_fees"] +
        collection["medicine_charges"]
    )

    # Render Dashboard
    return render_template(
        "dashboard.html",

        total_patients=total_patients,
        total_visits=total_visits,

        today_patients=today_patients,
        weekly_patients=weekly_patients,
        monthly_patients=monthly_patients,

        patient_types=patient_types,

        doctor_fees=collection["doctor_fees"],
        medicine_charges=collection["medicine_charges"],

        daily_doctor_fees=daily_collection["doctor_fees"],
        daily_medicine_charges=daily_collection["medicine_charges"],

        weekly_doctor_fees=weekly_collection["doctor_fees"],
        weekly_medicine_charges=weekly_collection["medicine_charges"],

        monthly_doctor_fees=monthly_collection["doctor_fees"],
        monthly_medicine_charges=monthly_collection["medicine_charges"],

        total_collection=total_collection,

        selected_date=selected_date,
        date_collection=date_collection,

        # Graph Data
        visit_chart_labels=visit_chart_labels,
        visit_chart_values=visit_chart_values,
        patient_type_labels=patient_type_labels,
        patient_type_values=patient_type_values,
        collection_chart_labels=collection_chart_labels,
        collection_chart_values=collection_chart_values
    )
# Collection Reports
@app.route("/reports")
@login_required
def reports():

    conn = get_db_connection()

    # Report Type
    report_type = request.args.get("period", "daily")

    if report_type not in ["daily", "weekly", "monthly"]:
        report_type = "daily"

    # Selected Date
    selected_date = request.args.get("report_date")

    try:
        report_date = (
            datetime.strptime(selected_date, "%Y-%m-%d").date()
            if selected_date
            else datetime.now().date()
        )

    except ValueError:
        report_date = datetime.now().date()

    selected_date = report_date.strftime("%Y-%m-%d")

    # Calculate Date Range
    if report_type == "daily":

        start_date = report_date
        end_date = report_date

    elif report_type == "weekly":

        start_date = report_date - timedelta(days=report_date.weekday())
        end_date = start_date + timedelta(days=6)

    else:

        start_date = report_date.replace(day=1)

        if report_date.month == 12:
            next_month = report_date.replace(
                year=report_date.year + 1,
                month=1,
                day=1
            )
        else:
            next_month = report_date.replace(
                month=report_date.month + 1,
                day=1
            )

        end_date = next_month - timedelta(days=1)

    start_date = start_date.strftime("%Y-%m-%d")
    end_date = end_date.strftime("%Y-%m-%d")

    try:

        # Collection Summary
        summary = conn.execute("""
            SELECT
                COUNT(*) AS total_visits,
                COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
                COALESCE(SUM(medicine_charges), 0) AS medicine_charges
            FROM visits
            WHERE visit_date BETWEEN ? AND ?
        """, (start_date, end_date)).fetchone()

        # Date-wise Collection
        daily_reports = conn.execute("""
            SELECT
                visit_date,
                COUNT(*) AS total_visits,
                COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
                COALESCE(SUM(medicine_charges), 0) AS medicine_charges
            FROM visits
            WHERE visit_date BETWEEN ? AND ?
            GROUP BY visit_date
            ORDER BY visit_date DESC
        """, (start_date, end_date)).fetchall()

    finally:
        conn.close()

    # Total Collection
    total_collection = (
        summary["doctor_fees"] + summary["medicine_charges"]
    )

    return render_template(
        "reports.html",
        report_type=report_type,
        selected_date=selected_date,
        start_date=start_date,
        end_date=end_date,
        summary=summary,
        total_collection=total_collection,
        daily_reports=daily_reports
    )


# Download Collection Reports (CSV)
@app.route("/download-reports")
@login_required
def download_reports():

    # Report Type
    report_type = request.args.get("period", "daily")

    if report_type not in ["daily", "weekly", "monthly"]:
        report_type = "daily"

    # Selected Date
    selected_date = request.args.get("report_date")

    try:
        report_date = (
            datetime.strptime(selected_date, "%Y-%m-%d").date()
            if selected_date
            else datetime.now().date()
        )

    except ValueError:
        report_date = datetime.now().date()

    # Calculate Date Range
    if report_type == "daily":

        start_date = report_date
        end_date = report_date

    elif report_type == "weekly":

        start_date = report_date - timedelta(days=report_date.weekday())
        end_date = start_date + timedelta(days=6)

    else:

        start_date = report_date.replace(day=1)

        if report_date.month == 12:
            next_month = report_date.replace(
                year=report_date.year + 1,
                month=1,
                day=1
            )
        else:
            next_month = report_date.replace(
                month=report_date.month + 1,
                day=1
            )

        end_date = next_month - timedelta(days=1)

    start_date = start_date.strftime("%Y-%m-%d")
    end_date = end_date.strftime("%Y-%m-%d")

    conn = get_db_connection()

    try:

        records = conn.execute("""
            SELECT
                visit_date,
                COUNT(*) AS total_visits,
                COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
                COALESCE(SUM(medicine_charges), 0) AS medicine_charges
            FROM visits
            WHERE visit_date BETWEEN ? AND ?
            GROUP BY visit_date
            ORDER BY visit_date
        """, (start_date, end_date)).fetchall()

    finally:
        conn.close()

    # Create CSV File
    output = StringIO()

    writer = csv.writer(output)

    writer.writerow([
        "Visit Date",
        "Total Visits",
        "Doctor Fees",
        "Medicine Charges",
        "Total Collection"
    ])

    for row in records:

        total = row["doctor_fees"] + row["medicine_charges"]

        writer.writerow([
            row["visit_date"],
            row["total_visits"],
            row["doctor_fees"],
            row["medicine_charges"],
            total
        ])

    csv_data = BytesIO(
        output.getvalue().encode("utf-8-sig")
    )

    filename = f"ClinicIQ_{report_type}_Collection_Report.csv"

    return send_file(
        csv_data,
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename
    )
# Add New Patient
@app.route("/add-patient", methods=["GET", "POST"])
@login_required
def add_patient():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        age = request.form.get("age", "").strip()
        gender = request.form.get("gender")
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()

        patient_type = request.form.get("patient_type")
        problem = request.form.get("problem", "").strip()
        treatment = request.form.get("treatment", "").strip()
        current_datetime = datetime.now()

        visit_date = current_datetime.strftime("%Y-%m-%d")
        visit_time = current_datetime.strftime("%H:%M:%S")

        doctor_fees = request.form.get("doctor_fees", "").strip() or "0"
        medicine_charges = request.form.get("medicine_charges", "").strip() or "0"

        try:
            age = int(age)
            doctor_fees = float(doctor_fees)
            medicine_charges = float(medicine_charges)

            if age < 0 or age > 120:
                raise ValueError

            if doctor_fees < 0 or medicine_charges < 0:
                raise ValueError

            if not name:
                raise ValueError

            if gender not in ["Male", "Female", "Other"]:
                raise ValueError

            if patient_type not in ["Normal", "Ear", "Dental"]:
                raise ValueError

        except ValueError:
            return render_template(
                "add_patient.html",
                error="Please enter valid patient details."
            )

        conn = get_db_connection()

        try:
            cursor = conn.execute("""
                INSERT INTO patients
                (name, age, gender, address, phone)
                VALUES (?, ?, ?, ?, ?)
            """, (name, age, gender, address, phone))

            patient_id = cursor.lastrowid

            conn.execute("""
                INSERT INTO visits
                (patient_id, patient_type, problem, treatment,
                visit_date, visit_time, doctor_fees, medicine_charges)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                patient_id,
                patient_type,
                problem,
                treatment,
                visit_date,
                visit_time,
                doctor_fees,
                medicine_charges
            ))

            conn.commit()

        except sqlite3.Error:
            conn.rollback()

            return render_template(
                "add_patient.html",
                error="Unable to save patient. Please try again."
            )

        finally:
            conn.close()

        return render_template(
            "add_patient.html",
            success="Patient added successfully!"
        )

    return render_template("add_patient.html")
# View All Patients
# All Patients with Search
@app.route("/patients")
@login_required
def patients():

    search = request.args.get("search", "").strip()

    conn = get_db_connection()

    if search:

        patients = conn.execute("""
            SELECT * FROM patients
            WHERE
                name LIKE ?
                OR phone LIKE ?
                OR CAST(id AS TEXT) LIKE ?
            ORDER BY id DESC
        """, (
            f"%{search}%",
            f"%{search}%",
            f"%{search}%"
        )).fetchall()

    else:

        patients = conn.execute("""
            SELECT * FROM patients
            ORDER BY id DESC
        """).fetchall()

    conn.close()

    return render_template(
        "patients.html",
        patients=patients,
        search=search
    )
# Patient History
@app.route("/patient-history/<int:patient_id>")
@login_required
def patient_history(patient_id):

    conn = get_db_connection()

    try:
        # Get Patient Details
        patient = conn.execute("""
            SELECT * FROM patients
            WHERE id = ?
        """, (patient_id,)).fetchone()

        if patient is None:
            return "Patient not found", 404

        # Get Patient Visits
        visits = conn.execute("""
            SELECT * FROM visits
            WHERE patient_id = ?
            ORDER BY visit_date DESC
        """, (patient_id,)).fetchall()

    finally:
        conn.close()

    return render_template(
        "history.html",
        patient=patient,
        visits=visits
    )
# Edit Patient
@app.route("/edit-patient/<int:patient_id>", methods=["GET", "POST"])
@login_required
def edit_patient(patient_id):

    conn = get_db_connection()

    try:
        # Find Patient
        patient = conn.execute("""
            SELECT * FROM patients
            WHERE id = ?
        """, (patient_id,)).fetchone()

        if patient is None:
            return "Patient not found", 404

        error = None

        if request.method == "POST":

            name = request.form.get("name", "").strip()
            age = request.form.get("age", "").strip()
            gender = request.form.get("gender", "").strip()
            address = request.form.get("address", "").strip()
            phone = request.form.get("phone", "").strip()

            # Validation
            if not name or not age or not gender:
                error = "Name, age and gender are required."

            elif not age.isdigit() or int(age) <= 0:
                error = "Please enter a valid age."

            elif gender not in ["Male", "Female", "Other"]:
                error = "Please select a valid gender."

            else:

                conn.execute("""
                    UPDATE patients
                    SET
                        name = ?,
                        age = ?,
                        gender = ?,
                        address = ?,
                        phone = ?
                    WHERE id = ?
                """, (
                    name,
                    int(age),
                    gender,
                    address,
                    phone,
                    patient_id
                ))

                conn.commit()

                return redirect(
                    url_for("patient_history", patient_id=patient_id)
                )

        return render_template(
            "edit_patient.html",
            patient=patient,
            error=error
        )

    finally:
        conn.close()

# Add New Visit for Existing Patient
@app.route("/add-visit/<int:patient_id>", methods=["GET", "POST"])
@login_required
def add_visit(patient_id):

    conn = get_db_connection()

    patient = conn.execute("""
        SELECT * FROM patients
        WHERE id = ?
    """, (patient_id,)).fetchone()

    conn.close()

    if patient is None:
        return "Patient not found", 404

    if request.method == "POST":

        patient_type = request.form.get("patient_type")
        problem = request.form.get("problem", "").strip()
        treatment = request.form.get("treatment", "").strip()
        current_datetime = datetime.now()

        visit_date = current_datetime.strftime("%Y-%m-%d")
        visit_time = current_datetime.strftime("%H:%M:%S")

        doctor_fees = request.form.get("doctor_fees", "").strip() or "0"
        medicine_charges = request.form.get("medicine_charges", "").strip() or "0"

        try:
            doctor_fees = float(doctor_fees)
            medicine_charges = float(medicine_charges)

            if doctor_fees < 0 or medicine_charges < 0:
                raise ValueError

            if patient_type not in ["Normal", "Ear", "Dental"]:
                raise ValueError

            if not visit_date:
                raise ValueError

        except ValueError:
            return render_template(
                "add_visit.html",
                patient=patient,
                error="Please enter valid visit details."
            )

        conn = get_db_connection()

        try:
            conn.execute("""
                INSERT INTO visits
                (patient_id, patient_type, problem, treatment,
                 visit_date, visit_time, doctor_fees, medicine_charges)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                patient_id,
                patient_type,
                problem,
                treatment,
                visit_date,
                visit_time,
                doctor_fees,
                medicine_charges
            ))

            conn.commit()

        except sqlite3.Error:
            conn.rollback()

            return render_template(
                "add_visit.html",
                patient=patient,
                error="Unable to save visit. Please try again."
            )

        finally:
            conn.close()

        return redirect(
            url_for("patient_history", patient_id=patient_id)
        )

    return render_template(
        "add_visit.html",
        patient=patient
    )
# Edit Visit
@app.route("/edit-visit/<int:visit_id>", methods=["GET", "POST"])
@login_required
def edit_visit(visit_id):

    conn = get_db_connection()

    try:
        visit = conn.execute("""
            SELECT * FROM visits
            WHERE id = ?
        """, (visit_id,)).fetchone()

        if visit is None:
            return "Visit not found", 404

        patient = conn.execute("""
            SELECT * FROM patients
            WHERE id = ?
        """, (visit["patient_id"],)).fetchone()

        error = None

        if request.method == "POST":

            patient_type = request.form.get("patient_type", "").strip()
            problem = request.form.get("problem", "").strip()
            treatment = request.form.get("treatment", "").strip()

            doctor_fees = request.form.get("doctor_fees", "").strip() or "0"
            medicine_charges = request.form.get("medicine_charges", "").strip() or "0"

            try:
                doctor_fees = float(doctor_fees)
                medicine_charges = float(medicine_charges)

                if patient_type not in ["Normal", "Ear", "Dental"]:
                    raise ValueError

                if doctor_fees < 0 or medicine_charges < 0:
                    raise ValueError

            except ValueError:
                error = "Please enter valid visit details."

            if error is None:

                conn.execute("""
                    UPDATE visits
                    SET patient_type = ?,
                        problem = ?,
                        treatment = ?,
                        doctor_fees = ?,
                        medicine_charges = ?
                    WHERE id = ?
                """, (
                    patient_type,
                    problem,
                    treatment,
                    doctor_fees,
                    medicine_charges,
                    visit_id
                ))

                conn.commit()

                return redirect(
                    url_for("patient_history", patient_id=visit["patient_id"])
                )

        return render_template(
            "edit_visit.html",
            visit=visit,
            patient=patient,
            error=error
        )
    finally:
        conn.close()
from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import os
import shutil
import csv
from io import StringIO, BytesIO
from flask import send_file

app = Flask(__name__)

app.secret_key = os.environ.get("CLINICIQ_SECRET_KEY") or os.urandom(32)

# Session Security Settings
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "database.db"


# Database Connection
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# Database Backup Function
def create_database_backup():

    backup_folder = BASE_DIR / "backups"
    backup_folder.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    backup_path = backup_folder / f"clinic_backup_{timestamp}.db"

    source_conn = sqlite3.connect(DATABASE)
    backup_conn = sqlite3.connect(backup_path)

    try:
        source_conn.backup(backup_conn)

    finally:
        source_conn.close()
        backup_conn.close()

    return backup_path
# Admin Login Required
def login_required(route_function):

    @wraps(route_function)
    def wrapper(*args, **kwargs):

        if "admin_id" not in session:
            return redirect(url_for("login"))

        return route_function(*args, **kwargs)

    return wrapper

# Create Database Tables
def create_tables():

    conn = get_db_connection()

    # Patients Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            age INTEGER NOT NULL,
            gender TEXT NOT NULL,
            address TEXT,
            phone TEXT
        )
    """)

    # Patient Visits Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            patient_type TEXT NOT NULL,
            problem TEXT,
            treatment TEXT,
            visit_date TEXT NOT NULL,
            doctor_fees REAL DEFAULT 0,
            medicine_charges REAL DEFAULT 0,

            FOREIGN KEY (patient_id)
            REFERENCES patients(id)
        )
    """)
        # Add visit_time column if it does not exist
    columns = conn.execute("PRAGMA table_info(visits)").fetchall()

    column_names = [column["name"] for column in columns]

    if "visit_time" not in column_names:
        conn.execute("""
            ALTER TABLE visits
            ADD COLUMN visit_time TEXT
        """)

        # Admin Table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
# Check Admin Session Inactivity
@app.before_request
def check_session_timeout():

    if "admin_id" in session:

        current_time = datetime.now().timestamp()

        last_activity = session.get("last_activity", current_time)

        # Check 30 Minutes Inactivity
        if current_time - last_activity > 30 * 60:

            session.clear()

            return redirect(url_for("login"))

        # Update Last Activity Time
        session["last_activity"] = current_time

# Admin Login
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db_connection()

        admin = conn.execute(
            "SELECT * FROM admins WHERE username = ?",
            (username,)
        ).fetchone()

        conn.close()

        if admin and check_password_hash(admin["password_hash"], password):

            session.clear()
            session.permanent = True

            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            session["last_activity"] = datetime.now().timestamp()
            return redirect(url_for("dashboard"))

        return render_template(
            "login.html",
            error="Invalid username or password."
        )

    return render_template("login.html")


# Admin Logout
@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))
# Database Backup Route
@app.route("/backup")
@login_required

def backup_database():

    backup_path = create_database_backup()

    return f"Database backup created successfully: {backup_path.name}"

# Download Database Backup
@app.route("/download-backup")
@login_required
def download_backup():

    backup_path = create_database_backup()

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=backup_path.name
    )

# Database Restore Route
@app.route("/restore", methods=["GET", "POST"])
@login_required
def restore_database():

    backup_folder = BASE_DIR / "backups"

    backups = sorted(
        [file.name for file in backup_folder.glob("*.db")],
        reverse=True
    )

    if request.method == "POST":

        backup_file = request.form.get("backup_file", "")

        # Validate Backup File
        if not backup_file or Path(backup_file).name != backup_file:
            return render_template(
                "restore.html",
                backups=backups,
                error="Invalid backup file."
            )

        selected_backup = backup_folder / backup_file

        if not selected_backup.is_file():
            return render_template(
                "restore.html",
                backups=backups,
                error="Backup file not found."
            )
        if selected_backup.suffix.lower() != ".db":
            return render_template(
                "restore.html",
                backups=backups,
                error="Only database backup files are allowed."
            )

        try:
            # Check Database Integrity
            check_conn = sqlite3.connect(
                f"file:{selected_backup.as_posix()}?mode=ro",
                uri=True
            )

            integrity = check_conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            tables = {
                row[0] for row in check_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

            check_conn.close()

            required_tables = {"patients", "visits", "admins"}

            if integrity != "ok" or not required_tables.issubset(tables):
                raise ValueError("Backup database is invalid.")

            # Create Safety Backup
            create_database_backup()

            # Restore Database
            temp_path = DATABASE.with_name("database_restore.tmp")

            shutil.copy2(selected_backup, temp_path)
            os.replace(temp_path, DATABASE)

            return render_template(
                "restore.html",
                backups=backups,
                success="Database restored successfully!"
            )

        except Exception:
            if 'temp_path' in locals() and temp_path.exists():
                temp_path.unlink()

            return render_template(
                "restore.html",
                backups=backups,
                error="Restore failed. Original database was not intentionally changed."
            )

    return render_template(
        "restore.html",
        backups=backups
    )

# Download Patient Records (CSV)
@app.route("/download-patients")
@login_required
def download_patients():

    conn = get_db_connection()

    try:
        records = conn.execute("""
            SELECT
                p.id,
                p.name,
                p.age,
                p.gender,
                p.address,
                p.phone,
                v.patient_type,
                v.problem,
                v.treatment,
                v.visit_date,
                v.visit_time,
                v.doctor_fees,
                v.medicine_charges
            FROM patients p
            LEFT JOIN visits v ON p.id = v.patient_id
            ORDER BY p.id, v.visit_date
        """).fetchall()

    finally:
        conn.close()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Patient ID",
        "Name",
        "Age",
        "Gender",
        "Address",
        "Phone",
        "Patient Type",
        "Problem",
        "Treatment",
        "Visit Date",
        "Visit Time",
        "Doctor Fees",
        "Medicine Charges"
    ])

    for row in records:
        writer.writerow(list(row))

    csv_data = BytesIO(output.getvalue().encode("utf-8-sig"))

    return send_file(
        csv_data,
        mimetype="text/csv",
        as_attachment=True,
        download_name="ClinicIQ_Patient_Records.csv"
    )

@app.route("/")
def home():
    return redirect(url_for("dashboard"))

# Dashboard
@app.route("/dashboard")
@login_required
def dashboard():

    conn = get_db_connection()

    # Total Patients
    total_patients = conn.execute("""
        SELECT COUNT(*) FROM patients
    """).fetchone()[0]

    # Total Visits
    total_visits = conn.execute("""
        SELECT COUNT(*) FROM visits
    """).fetchone()[0]

    # Today's Patients
    today_patients = conn.execute("""
        SELECT COUNT(DISTINCT patient_id)
        FROM visits
        WHERE visit_date = DATE('now', 'localtime')
    """).fetchone()[0]

    # This Week's Patients
    weekly_patients = conn.execute("""
        SELECT COUNT(DISTINCT patient_id)
        FROM visits
        WHERE visit_date >= DATE(
            'now', 'localtime',
            'weekday 0', '-6 days'
        )
        AND visit_date <= DATE('now', 'localtime')
    """).fetchone()[0]

    # This Month's Patients
    monthly_patients = conn.execute("""
        SELECT COUNT(DISTINCT patient_id)
        FROM visits
        WHERE visit_date >= DATE(
            'now', 'localtime',
            'start of month'
        )
        AND visit_date <= DATE('now', 'localtime')
    """).fetchone()[0]

    # Patient Type Count
    patient_types = conn.execute("""
        SELECT patient_type, COUNT(*) AS total
        FROM visits
        GROUP BY patient_type
    """).fetchall()

    # Collection Details
    collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges
        FROM visits
    """).fetchone()

    # Daily Collection
    daily_collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges
        FROM visits
        WHERE visit_date = DATE('now', 'localtime')
    """).fetchone()

    # Weekly Collection
    weekly_collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges
        FROM visits
        WHERE visit_date >= DATE(
            'now', 'localtime',
            '-' || (
                (CAST(STRFTIME('%w', 'now', 'localtime') AS INTEGER) + 6) % 7
            ) || ' days'
        )
        AND visit_date <= DATE('now', 'localtime')
    """).fetchone()

    # Monthly Collection
    monthly_collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges
        FROM visits
        WHERE visit_date >= DATE(
            'now', 'localtime', 'start of month'
        )
        AND visit_date <= DATE('now', 'localtime')
    """).fetchone()

    # Date-wise Collection
    selected_date = request.args.get("report_date")

    if not selected_date:
        selected_date = datetime.now().strftime("%Y-%m-%d")

    date_collection = conn.execute("""
        SELECT
            COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
            COALESCE(SUM(medicine_charges), 0) AS medicine_charges,
            COUNT(*) AS total_visits
        FROM visits
        WHERE visit_date = ?
    """, (selected_date,)).fetchone()

    # Last 7 Days Patient Visits Graph
    weekly_visit_data = conn.execute("""
        SELECT
            visit_date,
            COUNT(*) AS total
        FROM visits
        WHERE visit_date >= DATE('now', 'localtime', '-6 days')
        AND visit_date <= DATE('now', 'localtime')
        GROUP BY visit_date
    """).fetchall()

    visit_counts = {
        row["visit_date"]: row["total"]
        for row in weekly_visit_data
    }

    today = datetime.now().date()

    visit_chart_labels = [
        (today - timedelta(days=i)).strftime("%d %b")
        for i in range(6, -1, -1)
    ]

    visit_chart_values = [
        visit_counts.get(
            (today - timedelta(days=i)).strftime("%Y-%m-%d"),
            0
        )
        for i in range(6, -1, -1)
    ]

    # Patient Categories Graph
    patient_type_labels = [
        item["patient_type"] for item in patient_types
    ]

    patient_type_values = [
        item["total"] for item in patient_types
    ]

    # Collection Comparison Graph
    collection_chart_labels = ["Doctor Fees", "Medicine Charges"]
    collection_chart_values = [
        collection["doctor_fees"],
        collection["medicine_charges"]
    ]

    conn.close()

    # Total Collection
    total_collection = (
        collection["doctor_fees"] +
        collection["medicine_charges"]
    )

    # Render Dashboard
    return render_template(
        "dashboard.html",

        total_patients=total_patients,
        total_visits=total_visits,

        today_patients=today_patients,
        weekly_patients=weekly_patients,
        monthly_patients=monthly_patients,

        patient_types=patient_types,

        doctor_fees=collection["doctor_fees"],
        medicine_charges=collection["medicine_charges"],

        daily_doctor_fees=daily_collection["doctor_fees"],
        daily_medicine_charges=daily_collection["medicine_charges"],

        weekly_doctor_fees=weekly_collection["doctor_fees"],
        weekly_medicine_charges=weekly_collection["medicine_charges"],

        monthly_doctor_fees=monthly_collection["doctor_fees"],
        monthly_medicine_charges=monthly_collection["medicine_charges"],

        total_collection=total_collection,

        selected_date=selected_date,
        date_collection=date_collection,

        # Graph Data
        visit_chart_labels=visit_chart_labels,
        visit_chart_values=visit_chart_values,
        patient_type_labels=patient_type_labels,
        patient_type_values=patient_type_values,
        collection_chart_labels=collection_chart_labels,
        collection_chart_values=collection_chart_values
    )
# Collection Reports
@app.route("/reports")
@login_required
def reports():

    conn = get_db_connection()

    # Report Type
    report_type = request.args.get("period", "daily")

    if report_type not in ["daily", "weekly", "monthly"]:
        report_type = "daily"

    # Selected Date
    selected_date = request.args.get("report_date")

    try:
        report_date = (
            datetime.strptime(selected_date, "%Y-%m-%d").date()
            if selected_date
            else datetime.now().date()
        )

    except ValueError:
        report_date = datetime.now().date()

    selected_date = report_date.strftime("%Y-%m-%d")

    # Calculate Date Range
    if report_type == "daily":

        start_date = report_date
        end_date = report_date

    elif report_type == "weekly":

        start_date = report_date - timedelta(days=report_date.weekday())
        end_date = start_date + timedelta(days=6)

    else:

        start_date = report_date.replace(day=1)

        if report_date.month == 12:
            next_month = report_date.replace(
                year=report_date.year + 1,
                month=1,
                day=1
            )
        else:
            next_month = report_date.replace(
                month=report_date.month + 1,
                day=1
            )

        end_date = next_month - timedelta(days=1)

    start_date = start_date.strftime("%Y-%m-%d")
    end_date = end_date.strftime("%Y-%m-%d")

    try:

        # Collection Summary
        summary = conn.execute("""
            SELECT
                COUNT(*) AS total_visits,
                COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
                COALESCE(SUM(medicine_charges), 0) AS medicine_charges
            FROM visits
            WHERE visit_date BETWEEN ? AND ?
        """, (start_date, end_date)).fetchone()

        # Date-wise Collection
        daily_reports = conn.execute("""
            SELECT
                visit_date,
                COUNT(*) AS total_visits,
                COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
                COALESCE(SUM(medicine_charges), 0) AS medicine_charges
            FROM visits
            WHERE visit_date BETWEEN ? AND ?
            GROUP BY visit_date
            ORDER BY visit_date DESC
        """, (start_date, end_date)).fetchall()

    finally:
        conn.close()

    # Total Collection
    total_collection = (
        summary["doctor_fees"] + summary["medicine_charges"]
    )

    return render_template(
        "reports.html",
        report_type=report_type,
        selected_date=selected_date,
        start_date=start_date,
        end_date=end_date,
        summary=summary,
        total_collection=total_collection,
        daily_reports=daily_reports
    )


# Download Collection Reports (CSV)
@app.route("/download-reports")
@login_required
def download_reports():

    # Report Type
    report_type = request.args.get("period", "daily")

    if report_type not in ["daily", "weekly", "monthly"]:
        report_type = "daily"

    # Selected Date
    selected_date = request.args.get("report_date")

    try:
        report_date = (
            datetime.strptime(selected_date, "%Y-%m-%d").date()
            if selected_date
            else datetime.now().date()
        )

    except ValueError:
        report_date = datetime.now().date()

    # Calculate Date Range
    if report_type == "daily":

        start_date = report_date
        end_date = report_date

    elif report_type == "weekly":

        start_date = report_date - timedelta(days=report_date.weekday())
        end_date = start_date + timedelta(days=6)

    else:

        start_date = report_date.replace(day=1)

        if report_date.month == 12:
            next_month = report_date.replace(
                year=report_date.year + 1,
                month=1,
                day=1
            )
        else:
            next_month = report_date.replace(
                month=report_date.month + 1,
                day=1
            )

        end_date = next_month - timedelta(days=1)

    start_date = start_date.strftime("%Y-%m-%d")
    end_date = end_date.strftime("%Y-%m-%d")

    conn = get_db_connection()

    try:

        records = conn.execute("""
            SELECT
                visit_date,
                COUNT(*) AS total_visits,
                COALESCE(SUM(doctor_fees), 0) AS doctor_fees,
                COALESCE(SUM(medicine_charges), 0) AS medicine_charges
            FROM visits
            WHERE visit_date BETWEEN ? AND ?
            GROUP BY visit_date
            ORDER BY visit_date
        """, (start_date, end_date)).fetchall()

    finally:
        conn.close()

    # Create CSV File
    output = StringIO()

    writer = csv.writer(output)

    writer.writerow([
        "Visit Date",
        "Total Visits",
        "Doctor Fees",
        "Medicine Charges",
        "Total Collection"
    ])

    for row in records:

        total = row["doctor_fees"] + row["medicine_charges"]

        writer.writerow([
            row["visit_date"],
            row["total_visits"],
            row["doctor_fees"],
            row["medicine_charges"],
            total
        ])

    csv_data = BytesIO(
        output.getvalue().encode("utf-8-sig")
    )

    filename = f"ClinicIQ_{report_type}_Collection_Report.csv"

    return send_file(
        csv_data,
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename
    )
# Add New Patient
@app.route("/add-patient", methods=["GET", "POST"])
@login_required
def add_patient():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        age = request.form.get("age", "").strip()
        gender = request.form.get("gender")
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()

        patient_type = request.form.get("patient_type")
        problem = request.form.get("problem", "").strip()
        treatment = request.form.get("treatment", "").strip()
        current_datetime = datetime.now()

        visit_date = current_datetime.strftime("%Y-%m-%d")
        visit_time = current_datetime.strftime("%H:%M:%S")

        doctor_fees = request.form.get("doctor_fees", "").strip() or "0"
        medicine_charges = request.form.get("medicine_charges", "").strip() or "0"

        try:
            age = int(age)
            doctor_fees = float(doctor_fees)
            medicine_charges = float(medicine_charges)

            if age < 0 or age > 120:
                raise ValueError

            if doctor_fees < 0 or medicine_charges < 0:
                raise ValueError

            if not name:
                raise ValueError

            if gender not in ["Male", "Female", "Other"]:
                raise ValueError

            if patient_type not in ["Normal", "Ear", "Dental"]:
                raise ValueError

        except ValueError:
            return render_template(
                "add_patient.html",
                error="Please enter valid patient details."
            )

        conn = get_db_connection()

        try:
            cursor = conn.execute("""
                INSERT INTO patients
                (name, age, gender, address, phone)
                VALUES (?, ?, ?, ?, ?)
            """, (name, age, gender, address, phone))

            patient_id = cursor.lastrowid

            conn.execute("""
                INSERT INTO visits
                (patient_id, patient_type, problem, treatment,
                visit_date, visit_time, doctor_fees, medicine_charges)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                patient_id,
                patient_type,
                problem,
                treatment,
                visit_date,
                visit_time,
                doctor_fees,
                medicine_charges
            ))

            conn.commit()

        except sqlite3.Error:
            conn.rollback()

            return render_template(
                "add_patient.html",
                error="Unable to save patient. Please try again."
            )

        finally:
            conn.close()

        return render_template(
            "add_patient.html",
            success="Patient added successfully!"
        )

    return render_template("add_patient.html")
# View All Patients
# All Patients with Search
@app.route("/patients")
@login_required
def patients():

    search = request.args.get("search", "").strip()

    conn = get_db_connection()

    if search:

        patients = conn.execute("""
            SELECT * FROM patients
            WHERE
                name LIKE ?
                OR phone LIKE ?
                OR CAST(id AS TEXT) LIKE ?
            ORDER BY id DESC
        """, (
            f"%{search}%",
            f"%{search}%",
            f"%{search}%"
        )).fetchall()

    else:

        patients = conn.execute("""
            SELECT * FROM patients
            ORDER BY id DESC
        """).fetchall()

    conn.close()

    return render_template(
        "patients.html",
        patients=patients,
        search=search
    )
# Patient History
@app.route("/patient-history/<int:patient_id>")
@login_required
def patient_history(patient_id):

    conn = get_db_connection()

    try:
        # Get Patient Details
        patient = conn.execute("""
            SELECT * FROM patients
            WHERE id = ?
        """, (patient_id,)).fetchone()

        if patient is None:
            return "Patient not found", 404

        # Get Patient Visits
        visits = conn.execute("""
            SELECT * FROM visits
            WHERE patient_id = ?
            ORDER BY visit_date DESC
        """, (patient_id,)).fetchall()

    finally:
        conn.close()

    return render_template(
        "history.html",
        patient=patient,
        visits=visits
    )
# Edit Patient
@app.route("/edit-patient/<int:patient_id>", methods=["GET", "POST"])
@login_required
def edit_patient(patient_id):

    conn = get_db_connection()

    try:
        # Find Patient
        patient = conn.execute("""
            SELECT * FROM patients
            WHERE id = ?
        """, (patient_id,)).fetchone()

        if patient is None:
            return "Patient not found", 404

        error = None

        if request.method == "POST":

            name = request.form.get("name", "").strip()
            age = request.form.get("age", "").strip()
            gender = request.form.get("gender", "").strip()
            address = request.form.get("address", "").strip()
            phone = request.form.get("phone", "").strip()

            # Validation
            if not name or not age or not gender:
                error = "Name, age and gender are required."

            elif not age.isdigit() or int(age) <= 0:
                error = "Please enter a valid age."

            elif gender not in ["Male", "Female", "Other"]:
                error = "Please select a valid gender."

            else:

                conn.execute("""
                    UPDATE patients
                    SET
                        name = ?,
                        age = ?,
                        gender = ?,
                        address = ?,
                        phone = ?
                    WHERE id = ?
                """, (
                    name,
                    int(age),
                    gender,
                    address,
                    phone,
                    patient_id
                ))

                conn.commit()

                return redirect(
                    url_for("patient_history", patient_id=patient_id)
                )

        return render_template(
            "edit_patient.html",
            patient=patient,
            error=error
        )

    finally:
        conn.close()

# Add New Visit for Existing Patient
@app.route("/add-visit/<int:patient_id>", methods=["GET", "POST"])
@login_required
def add_visit(patient_id):

    conn = get_db_connection()

    patient = conn.execute("""
        SELECT * FROM patients
        WHERE id = ?
    """, (patient_id,)).fetchone()

    conn.close()

    if patient is None:
        return "Patient not found", 404

    if request.method == "POST":

        patient_type = request.form.get("patient_type")
        problem = request.form.get("problem", "").strip()
        treatment = request.form.get("treatment", "").strip()
        current_datetime = datetime.now()

        visit_date = current_datetime.strftime("%Y-%m-%d")
        visit_time = current_datetime.strftime("%H:%M:%S")

        doctor_fees = request.form.get("doctor_fees", "").strip() or "0"
        medicine_charges = request.form.get("medicine_charges", "").strip() or "0"

        try:
            doctor_fees = float(doctor_fees)
            medicine_charges = float(medicine_charges)

            if doctor_fees < 0 or medicine_charges < 0:
                raise ValueError

            if patient_type not in ["Normal", "Ear", "Dental"]:
                raise ValueError

            if not visit_date:
                raise ValueError

        except ValueError:
            return render_template(
                "add_visit.html",
                patient=patient,
                error="Please enter valid visit details."
            )

        conn = get_db_connection()

        try:
            conn.execute("""
                INSERT INTO visits
                (patient_id, patient_type, problem, treatment,
                 visit_date, visit_time, doctor_fees, medicine_charges)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                patient_id,
                patient_type,
                problem,
                treatment,
                visit_date,
                visit_time,
                doctor_fees,
                medicine_charges
            ))

            conn.commit()

        except sqlite3.Error:
            conn.rollback()

            return render_template(
                "add_visit.html",
                patient=patient,
                error="Unable to save visit. Please try again."
            )

        finally:
            conn.close()

        return redirect(
            url_for("patient_history", patient_id=patient_id)
        )

    return render_template(
        "add_visit.html",
        patient=patient
    )
# Edit Visit
@app.route("/edit-visit/<int:visit_id>", methods=["GET", "POST"])
@login_required
def edit_visit(visit_id):

    conn = get_db_connection()

    try:
        visit = conn.execute("""
            SELECT * FROM visits
            WHERE id = ?
        """, (visit_id,)).fetchone()

        if visit is None:
            return "Visit not found", 404

        patient = conn.execute("""
            SELECT * FROM patients
            WHERE id = ?
        """, (visit["patient_id"],)).fetchone()

        error = None

        if request.method == "POST":

            patient_type = request.form.get("patient_type", "").strip()
            problem = request.form.get("problem", "").strip()
            treatment = request.form.get("treatment", "").strip()

            doctor_fees = request.form.get("doctor_fees", "").strip() or "0"
            medicine_charges = request.form.get("medicine_charges", "").strip() or "0"

            try:
                doctor_fees = float(doctor_fees)
                medicine_charges = float(medicine_charges)

                if patient_type not in ["Normal", "Ear", "Dental"]:
                    raise ValueError

                if doctor_fees < 0 or medicine_charges < 0:
                    raise ValueError

            except ValueError:
                error = "Please enter valid visit details."

            if error is None:

                conn.execute("""
                    UPDATE visits
                    SET patient_type = ?,
                        problem = ?,
                        treatment = ?,
                        doctor_fees = ?,
                        medicine_charges = ?
                    WHERE id = ?
                """, (
                    patient_type,
                    problem,
                    treatment,
                    doctor_fees,
                    medicine_charges,
                    visit_id
                ))

                conn.commit()

                return redirect(
                    url_for("patient_history", patient_id=visit["patient_id"])
                )

        return render_template(
            "edit_visit.html",
            visit=visit,
            patient=patient,
            error=error
        )
    finally:
        conn.close()
# Delete Single Patient
@app.route("/delete-patient/<int:patient_id>", methods=["POST"])
@login_required
def delete_patient(patient_id):

    conn = get_db_connection()

    try:
        patient = conn.execute("""
            SELECT id FROM patients
            WHERE id = ?
        """, (patient_id,)).fetchone()

    finally:
        conn.close()

    if patient is None:
        return "Patient not found", 404

    try:
        # Create backup before deleting
        create_database_backup()

        conn = get_db_connection()

        try:
            conn.execute("BEGIN IMMEDIATE")

            # Delete patient's visits
            conn.execute("""
                DELETE FROM visits
                WHERE patient_id = ?
            """, (patient_id,))

            # Delete patient
            conn.execute("""
                DELETE FROM patients
                WHERE id = ?
            """, (patient_id,))

            conn.commit()

        except Exception:
            conn.rollback()
            return "Unable to delete patient.", 500

        finally:
            conn.close()

    except Exception:
        return "Backup failed. Patient was not deleted.", 500

    return redirect(url_for("patients"))

# Delete Patients by Month
@app.route("/delete-monthly-patients", methods=["POST"])
@login_required
def delete_monthly_patients():

    selected_month = request.form.get("month", "").strip()

    # Validate selected month
    try:
        datetime.strptime(selected_month, "%Y-%m")
    except ValueError:
        return "Please select a valid month.", 400

    conn = get_db_connection()

    try:
        # Find patients who visited during selected month
        patient_ids = conn.execute("""
            SELECT DISTINCT patient_id
            FROM visits
            WHERE visit_date LIKE ?
        """, (f"{selected_month}-%",)).fetchall()

    finally:
        conn.close()

    # No records found
    if not patient_ids:
        return redirect(url_for("patients"))

    try:
        # Create backup before deletion
        create_database_backup()

        conn = get_db_connection()

        try:
            conn.execute("BEGIN IMMEDIATE")

            # Delete visits of selected month
            conn.execute("""
                DELETE FROM visits
                WHERE visit_date LIKE ?
            """, (f"{selected_month}-%",))

            # Delete patients who have no visits remaining
            for row in patient_ids:

                remaining_visits = conn.execute("""
                    SELECT COUNT(*)
                    FROM visits
                    WHERE patient_id = ?
                """, (row["patient_id"],)).fetchone()[0]

                if remaining_visits == 0:

                    conn.execute("""
                        DELETE FROM patients
                        WHERE id = ?
                    """, (row["patient_id"],))

            conn.commit()

        except Exception:
            conn.rollback()
            return "Unable to delete monthly records.", 500

        finally:
            conn.close()

    except Exception:
        return "Backup failed. Records were not deleted.", 500

    return redirect(url_for("patients"))

# Initialize database tables
create_tables()


if __name__ == "__main__":

    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
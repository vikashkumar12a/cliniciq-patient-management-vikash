# ClinicIQ – Patient Management System

ClinicIQ is a web-based Patient Management System developed using Python Flask and SQLite.

It helps doctors manage patient records, medical visits, and collection reports through a simple dashboard.

## Features

- Admin Login and Authentication
- Patient Registration
- View All Patients
- Search Patients
- Edit Patient Details
- Patient Medical History
- Add New Visits
- Doctor Fees and Medicine Charges Management
- Daily, Weekly and Monthly Collection Reports
- Download Patient Records as CSV
- Download Collection Reports as CSV
- Database Backup and Restore
- Dashboard Analytics and Charts
- Form Validation
- Session Timeout

## Technologies Used

- Python
- Flask
- SQLite
- HTML
- CSS
- JavaScript
- Chart.js

## Installation

1. Clone the repository.

2. Open the project folder.

3. Create a virtual environment:

   `python -m venv venv`

4. Activate the virtual environment:

   `venv\Scripts\activate`

5. Install dependencies:

   `python -m pip install -r requirements.txt`

6. Create the admin account:

   `python create_admin.py`

7. Start the application:

   `python app.py`

8. Open your browser:

   `http://127.0.0.1:5000`

## Project Structure

```text
ClinicIQ/
│
├── app.py
├── create_admin.py
├── database.db
├── requirements.txt
│
├── backups/
│
├── templates/
│   ├── dashboard.html
│   ├── login.html
│   ├── add_patient.html
│   ├── patients.html
│   ├── history.html
│   ├── add_visit.html
│   ├── edit_patient.html
│   ├── edit_visit.html
│   ├── reports.html
│   └── restore.html
│
└── static/
    └── style.css
```

## Security Notes

- Keep admin credentials private.
- Configure a secure Flask secret key using `CLINICIQ_SECRET_KEY`.
- Keep debug mode disabled in production.
- Use HTTPS when deploying.
- Never upload private patient information or production database backups to a public repository.

## Developer

Vikash Kumar

B.Tech Computer Science Student

## Project Status

ClinicIQ is an ongoing project. Additional improvements and testing are planned.
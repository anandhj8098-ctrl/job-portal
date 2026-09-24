# JobConnect

JobConnect is a Flask + SQLite demo platform for IT job search, profile management, recruiter workflows, and admin monitoring.

## Features

- Job seeker registration and login
- Profile creation and skill/experience/education/project management
- Resume upload and resume management
- Job search, filters, recommendations, and saved jobs
- Job applications with status tracking
- Interview scheduling and interview detail pages
- Recruiter dashboard and job posting tools
- Admin dashboard and basic management views
- Demo data for Josewa and fictional IT companies/jobs

## Project structure

- `backend/app.py` – Flask routes and app logic
- `backend/database.py` – SQLite schema and bootstrap
- `frontend/templates/` – HTML templates
- `frontend/static/` – CSS and JS assets
- `database/jobconnect.db` – SQLite database
- `uploads/resumes/` – uploaded resumes

## Install dependencies

From the repository root:

```bash
python -m venv .venv
. .venv/bin/activate    # Linux/macOS
# or .venv\Scripts\activate  # Windows PowerShell
pip install -r requirements.txt
```

## Set up the database

The database is created automatically when the app starts. There is no manual migration step needed.

## Start locally

```bash
python "job portal/job portal/backend/app.py"
```

The app will run at:

```text
http://localhost:5000
```

## Demo accounts

Job seeker:
- Username: `josewa`
- Email: `josewa@example.com`
- Password: `Josewa@123`

Recruiter:
- Username: `recruiter`
- Email: `recruiter@example.com`
- Password: `Recruiter@123`

Admin:
- Username: `admin`
- Email: `admin@example.com`
- Password: `Admin@123`

## Landing page and login

Open the application in a browser:

```text
http://localhost:5000
```

Use the login form or the demo credentials above.

## Deploy

This is a Flask server, so it cannot run on GitHub Pages. Use a Python host
such as Render, Railway, or Fly.io connected to this GitHub repository.

The repository includes a root `Procfile` and `requirements.txt` for hosts
that auto-detect them. The production command is:

```bash
gunicorn --chdir "job portal/job portal" --bind 0.0.0.0:$PORT backend.app:app
```

Set `JOBCONNECT_SECRET_KEY` to a long random value in the host environment.
The bundled SQLite database and uploaded resumes are suitable for a demo only;
use persistent storage or a managed database for production.

## Notes

- All demo data is fictional.
- Resume uploads are restricted to PDF, DOC, and DOCX files.
- Uploaded resumes are stored in `uploads/resumes` and are not publicly exposed.
"# job-portal" 
"# job-portal" 

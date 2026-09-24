import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "database" / "jobconnect.db"
UPLOAD_DIR = BASE_DIR / "uploads" / "resumes"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            location TEXT,
            profile_image TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            headline TEXT,
            about TEXT,
            location TEXT,
            portfolio_url TEXT,
            github_url TEXT,
            linkedin_url TEXT,
            profile_completion INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            skill_id INTEGER NOT NULL,
            proficiency TEXT DEFAULT 'Intermediate',
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(skill_id) REFERENCES skills(id)
        );

        CREATE TABLE IF NOT EXISTS education (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            institution TEXT,
            degree TEXT,
            field TEXT,
            start_year INTEGER,
            end_year INTEGER,
            grade TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS experience (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            company TEXT,
            job_title TEXT,
            start_date TEXT,
            end_date TEXT,
            description TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            project_name TEXT,
            description TEXT,
            technologies TEXT,
            github_url TEXT,
            demo_url TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS certifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            certificate_name TEXT,
            organization TEXT,
            issue_date TEXT,
            credential_id TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT,
            file_size INTEGER,
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_default INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recruiter_id INTEGER,
            company_name TEXT NOT NULL,
            logo TEXT,
            description TEXT,
            industry TEXT,
            location TEXT,
            website TEXT,
            employee_count INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(recruiter_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            recruiter_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            responsibilities TEXT,
            required_skills TEXT,
            preferred_skills TEXT,
            location TEXT,
            salary_min INTEGER,
            salary_max INTEGER,
            employment_type TEXT,
            workplace_type TEXT,
            experience_level TEXT,
            education TEXT,
            deadline TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(recruiter_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS saved_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, job_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            resume_id INTEGER,
            cover_letter TEXT,
            portfolio_url TEXT,
            github_url TEXT,
            linkedin_url TEXT,
            additional_info TEXT,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'applied',
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(job_id) REFERENCES jobs(id),
            FOREIGN KEY(resume_id) REFERENCES resumes(id)
        );

        CREATE TABLE IF NOT EXISTS interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            interview_type TEXT,
            interview_date TEXT,
            interview_time TEXT,
            duration TEXT,
            interviewer TEXT,
            meeting_link TEXT,
            instructions TEXT,
            status TEXT DEFAULT 'scheduled',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(application_id) REFERENCES applications(id)
        );

        CREATE TABLE IF NOT EXISTS job_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            keywords TEXT,
            location TEXT,
            employment_type TEXT,
            experience_level TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()
    conn.close()

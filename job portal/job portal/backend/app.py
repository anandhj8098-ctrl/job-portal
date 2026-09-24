import json
import os
import sqlite3
import uuid
from functools import wraps
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from backend.database import DB_PATH, UPLOAD_DIR, get_db_connection, init_db
except ModuleNotFoundError:
    from database import DB_PATH, UPLOAD_DIR, get_db_connection, init_db

app = Flask(__name__, template_folder="../frontend/templates", static_folder="../frontend/static")
app.config["SECRET_KEY"] = os.environ.get("JOBCONNECT_SECRET_KEY") or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx"}


def resolve_resume_path(file_path):
    path = Path(file_path)
    if path.is_file():
        return path
    legacy_name = Path(str(file_path).replace("\\", "/")).name
    fallback = UPLOAD_DIR / legacy_name
    return fallback if fallback.is_file() else path


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in to continue.", "error")
                return redirect(url_for("login"))
            user = get_current_user()
            if user and user["role"] in roles:
                return view(*args, **kwargs)
            flash("You are not authorized to access this page.", "error")
            return redirect(url_for("dashboard"))

        return wrapped

    return decorator


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def get_user_profile(user_id):
    conn = get_db_connection()
    profile = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(profile) if profile else None


def add_notification(user_id, title, message):
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO notifications (user_id, title, message, is_read) VALUES (?, ?, ?, 0)",
        (user_id, title, message),
    )
    conn.commit()
    conn.close()


def compute_profile_completion(user_id):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    profile = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()

    percent = 0
    if user and user["full_name"] and user["email"] and user["phone"] and user["location"]:
        percent += 20
    if profile and profile["headline"]:
        percent += 10
    if profile and profile["about"]:
        percent += 10
    if conn.execute("SELECT COUNT(*) FROM user_skills WHERE user_id = ?", (user_id,)).fetchone()[0] > 0:
        percent += 20
    if conn.execute("SELECT COUNT(*) FROM education WHERE user_id = ?", (user_id,)).fetchone()[0] > 0:
        percent += 15
    if conn.execute("SELECT COUNT(*) FROM experience WHERE user_id = ?", (user_id,)).fetchone()[0] > 0:
        percent += 15
    if conn.execute("SELECT COUNT(*) FROM projects WHERE user_id = ?", (user_id,)).fetchone()[0] > 0:
        percent += 5
    if conn.execute("SELECT COUNT(*) FROM resumes WHERE user_id = ?", (user_id,)).fetchone()[0] > 0:
        percent += 5
    percent = min(percent, 100)
    conn.execute("UPDATE profiles SET profile_completion = ? WHERE user_id = ?", (percent, user_id))
    conn.commit()
    conn.close()
    return percent


def format_currency(amount):
    if not amount:
        return "Negotiable"
    return f"₹{amount} LPA"


def user_skill_names(user_id):
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT s.name FROM user_skills us
        JOIN skills s ON s.id = us.skill_id
        WHERE us.user_id = ?
        ORDER BY s.name
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return [row["name"] for row in rows]


def get_skill_matches(user_id):
    conn = get_db_connection()
    skills = set(user_skill_names(user_id))
    jobs = conn.execute(
        """
        SELECT j.*, c.company_name, c.logo
        FROM jobs j
        LEFT JOIN companies c ON c.id = j.company_id
        WHERE j.status = 'active'
        ORDER BY j.created_at DESC
        LIMIT 50
        """
    ).fetchall()
    results = []
    for job in jobs:
        job_data = dict(job)
        required = set((job_data.get("required_skills") or "").split(","))
        preferred = set((job_data.get("preferred_skills") or "").split(","))
        combined = {s.strip() for s in required | preferred if s.strip()}
        overlap = len(skills & combined)
        max_len = max(1, len(combined))
        match = min(98, max(30, round((overlap / max_len) * 100)))
        if overlap or "developer" in (job_data.get("title") or "").lower():
            results.append({"job": job_data, "match": match, "overlap": overlap})
    results.sort(key=lambda item: item["match"], reverse=True)
    conn.close()
    return results[:6]


def query_jobs(filters=None, q=None):
    conn = get_db_connection()
    conditions = ["j.status = 'active'"]
    params = []

    if q:
        conditions.append("(j.title LIKE ? OR j.description LIKE ? OR j.required_skills LIKE ? OR c.company_name LIKE ? OR j.location LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like, like])

    if filters:
        if filters.get("location"):
            conditions.append("j.location = ?")
            params.append(filters["location"])
        if filters.get("employment_type"):
            conditions.append("j.employment_type = ?")
            params.append(filters["employment_type"])
        if filters.get("experience_level"):
            conditions.append("j.experience_level = ?")
            params.append(filters["experience_level"])
        if filters.get("workplace_type"):
            conditions.append("j.workplace_type = ?")
            params.append(filters["workplace_type"])
        if filters.get("min_salary"):
            conditions.append("j.salary_min >= ?")
            params.append(int(filters["min_salary"]))
        if filters.get("max_salary"):
            conditions.append("j.salary_max <= ?")
            params.append(int(filters["max_salary"]))
        if filters.get("date_range"):
            if filters["date_range"] == "today":
                conditions.append("date(j.created_at) = date('now')")
            elif filters["date_range"] == "3days":
                conditions.append("date(j.created_at) >= date('now', '-3 days')")
            elif filters["date_range"] == "7days":
                conditions.append("date(j.created_at) >= date('now', '-7 days')")
            elif filters["date_range"] == "30days":
                conditions.append("date(j.created_at) >= date('now', '-30 days')")
        if filters.get("skills"):
            conditions.append("j.required_skills LIKE ?")
            params.append(f"%{filters['skills']}%")

    sql = f"""
        SELECT j.*, c.company_name, c.logo, c.industry,
               (SELECT COUNT(*) FROM applications a WHERE a.job_id = j.id) AS applicant_count
        FROM jobs j
        LEFT JOIN companies c ON c.id = j.company_id
        WHERE {' AND '.join(conditions)}
        ORDER BY j.created_at DESC
    """
    jobs = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in jobs]


def is_saved_by_user(user_id, job_id):
    if not user_id:
        return False
    conn = get_db_connection()
    row = conn.execute(
        "SELECT id FROM saved_jobs WHERE user_id = ? AND job_id = ?",
        (user_id, job_id),
    ).fetchone()
    conn.close()
    return bool(row)


def get_job_details(job_id):
    conn = get_db_connection()
    job = conn.execute(
        """
        SELECT j.*, c.company_name, c.logo, c.description AS company_description, c.location AS company_location,
               (SELECT COUNT(*) FROM applications a WHERE a.job_id = j.id) AS applicant_count
        FROM jobs j
        LEFT JOIN companies c ON c.id = j.company_id
        WHERE j.id = ? AND j.status = 'active'
        """,
        (job_id,),
    ).fetchone()
    conn.close()
    return dict(job) if job else None


def get_default_resume(user_id):
    conn = get_db_connection()
    resume = conn.execute(
        "SELECT * FROM resumes WHERE user_id = ? AND is_default = 1 ORDER BY uploaded_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if resume:
        conn.close()
        return dict(resume)
    resume = conn.execute(
        "SELECT * FROM resumes WHERE user_id = ? ORDER BY uploaded_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(resume) if resume else None


def ensure_demo_data():
    conn = get_db_connection()
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count > 0:
        conn.close()
        return

    admin_hash = generate_password_hash("Admin@123")
    recruiter_hash = generate_password_hash("Recruiter@123")
    josewa_hash = generate_password_hash("Josewa@123")

    conn.execute(
        "INSERT INTO users (username, full_name, email, phone, password_hash, role, location, profile_image) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("admin", "Platform Administrator", "admin@example.com", "+91 98765 43210", admin_hash, "admin", "Coimbatore", "admin.png"),
    )
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]

    conn.execute(
        "INSERT INTO users (username, full_name, email, phone, password_hash, role, location, profile_image) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("recruiter", "Neha Verma", "recruiter@example.com", "+91 99887 66554", recruiter_hash, "recruiter", "Bangalore", "recruiter.png"),
    )
    recruiter_id = conn.execute("SELECT id FROM users WHERE username = 'recruiter'").fetchone()[0]

    conn.execute(
        "INSERT INTO users (username, full_name, email, phone, password_hash, role, location, profile_image) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("josewa", "Josewa J", "josewa@example.com", "+91 88222 88990", josewa_hash, "job_seeker", "Coimbatore", "josewa.png"),
    )
    josewa_id = conn.execute("SELECT id FROM users WHERE username = 'josewa'").fetchone()[0]

    conn.execute(
        "INSERT INTO profiles (user_id, headline, about, location, portfolio_url, github_url, linkedin_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            josewa_id,
            "IT Student | Interested in Software and Web Development",
            "IT student with a strong interest in software development, web development, Python, and database development. Seeking entry-level opportunities in modern, product-focused teams.",
            "Coimbatore",
            "https://portfolio.demo/josewa",
            "https://github.com/josewa-demo",
            "https://linkedin.com/in/josewa-demo",
        ),
    )
    conn.execute(
        "INSERT INTO profiles (user_id, headline, about, location, portfolio_url, github_url, linkedin_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            recruiter_id,
            "Talent Acquisition Lead",
            "Experienced recruiter focused on building technical teams and hiring exceptional IT talent.",
            "Bangalore",
            "https://jobconnect.demo/companies",
            "https://github.com/jobconnect-demo",
            "https://linkedin.com/company/jobconnect-demo",
        ),
    )
    conn.execute(
        "INSERT INTO profiles (user_id, headline, about, location, portfolio_url, github_url, linkedin_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            admin_id,
            "Platform Administrator",
            "Responsible for maintaining platform quality, user policies, and operational insights.",
            "Coimbatore",
            "https://jobconnect.demo",
            "https://github.com/jobconnect-demo",
            "https://linkedin.com/company/jobconnect-demo",
        ),
    )

    skill_names = [
        "Python", "Java", "JavaScript", "React", "Node.js", "SQL", "MySQL", "Flask", "Django",
        "HTML", "CSS", "Machine Learning", "Data Analytics", "Cyber Security", "Software Development",
        "Web Development", "Java Spring", "MongoDB", "Azure", "AWS", "C++", "Git", "Tableau",
        "Docker", "Kubernetes", "REST APIs", "Data Structures", "Computer Networks", "UI/UX"
    ]
    for name in skill_names:
        conn.execute("INSERT OR IGNORE INTO skills (name) VALUES (?)", (name,))

    skill_map = {
        row["name"]: row["id"]
        for row in conn.execute("SELECT id, name FROM skills").fetchall()
    }
    josewa_skills = ["Python", "JavaScript", "SQL", "MySQL", "React", "Flask", "HTML", "CSS", "Git", "Data Structures"]
    for skill in josewa_skills:
        conn.execute(
            "INSERT INTO user_skills (user_id, skill_id, proficiency) VALUES (?, ?, ?)",
            (josewa_id, skill_map[skill], "Advanced" if skill in ["Python", "JavaScript", "SQL", "React"] else "Intermediate"),
        )

    conn.execute(
        "INSERT INTO education (user_id, institution, degree, field, start_year, end_year, grade) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (josewa_id, "PSG College of Technology", "B.Tech", "Information Technology", 2022, 2026, "8.6 CGPA"),
    )
    conn.execute(
        "INSERT INTO experience (user_id, company, job_title, start_date, end_date, description) VALUES (?, ?, ?, ?, ?, ?)",
        (josewa_id, "TechNest Labs", "Web Development Intern", "2025-01-01", "2025-06-30", "Built portfolio pages and improved performance for web applications using React and Flask."),
    )
    conn.execute(
        "INSERT INTO projects (user_id, project_name, description, technologies, github_url, demo_url) VALUES (?, ?, ?, ?, ?, ?)",
        (josewa_id, "Smart Attendance Tracker", "Designed a Python and Flask web app for attendance tracking with analytics dashboard.", "Python, Flask, SQLite, JavaScript", "https://github.com/josewa-demo/attendance", "https://demo.jobconnect/attendance"),
    )
    conn.execute(
        "INSERT INTO certifications (user_id, certificate_name, organization, issue_date, credential_id) VALUES (?, ?, ?, ?, ?)",
        (josewa_id, "Python for Everybody", "Coursera", "2024-11-15", "PY-2024-776"),
    )

    demo_resume_dir = Path(__file__).resolve().parent.parent / "uploads" / "resumes"
    demo_resume_dir.mkdir(parents=True, exist_ok=True)
    demo_resume_path = demo_resume_dir / "josewa_resume_demo.pdf"
    if not demo_resume_path.exists():
        demo_resume_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF")
    file_size = demo_resume_path.stat().st_size
    conn.execute(
        "INSERT INTO resumes (user_id, file_name, file_path, file_type, file_size, is_default) VALUES (?, ?, ?, ?, ?, 1)",
        (josewa_id, "josewa_resume_demo.pdf", str(demo_resume_path), "application/pdf", file_size),
    )

    companies = [
        ("TechNova Solutions", "technova.png", "An AI-first technology company focused on product engineering and cloud services.", "Software", "Bangalore", "https://technova.demo", 500),
        ("CodeSphere Technologies", "codesphere.png", "Building scalable digital experiences for startups and enterprises.", "IT Services", "Chennai", "https://codesphere.demo", 350),
        ("FutureSoft Systems", "futuresoft.png", "Produces enterprise software solutions for global customers.", "Software", "Hyderabad", "https://futuresoft.demo", 480),
        ("DataForge Labs", "dataforge.png", "Transforms business data into intelligent decision-making products.", "Data Analytics", "Pune", "https://dataforge.demo", 260),
        ("SecureGrid Systems", "securegrid.png", "Cyber security and infrastructure modernization specialist.", "Cyber Security", "Mumbai", "https://securegrid.demo", 200),
        ("CloudHive Solutions", "cloudhive.png", "Helping clients modernize cloud platforms and application delivery.", "Cloud", "Delhi", "https://cloudhive.demo", 300),
        ("PixelForge Studio", "pixelforge.png", "User-centered digital design and web product engineering.", "Design", "Coimbatore", "https://pixelforge.demo", 120),
        ("NovaStack Digital", "novastack.png", "Software engineering company creating AI-powered business tools.", "Fintech", "Remote", "https://novastack.demo", 400),
        ("InsightIQ Analytics", "insightiq.png", "Analytics and business intelligence platform provider.", "Data", "Bangalore", "https://insightiq.demo", 180),
        ("Aegis Security Labs", "aegis.png", "Security-focused product engineering and testing organization.", "Cyber Security", "Remote", "https://aegis.demo", 220),
    ]
    company_ids = {}
    for name, logo, desc, industry, location, website, employees in companies:
        conn.execute(
            "INSERT INTO companies (recruiter_id, company_name, logo, description, industry, location, website, employee_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (recruiter_id, name, logo, desc, industry, location, website, employees),
        )
        company_ids[name] = conn.execute("SELECT id FROM companies WHERE company_name = ?", (name,)).fetchone()[0]

    job_entries = [
        ("Python Developer", "TechNova Solutions", "Bangalore", 4, 6, "Hybrid", "Full Time", "Fresher", "Python, Flask, SQL, Git", "REST APIs, Django, MySQL", "B.Tech / B.E in Computer Science", "Python Developer", "Build and maintain Python-based APIs and internal tools.", "Develop Python applications, write APIs, improve data processing workflows.", "Software Developer"),
        ("Junior Web Developer", "CodeSphere Technologies", "Chennai", 3, 5, "On-site", "Full Time", "Fresher", "HTML, CSS, JavaScript, React", "UI/UX, REST APIs", "B.E/B.Tech or related field", "Web Developer", "Assist in front-end development of business portals and e-commerce features.", "Create responsive web interfaces and integrate APIs.", "Web Developer"),
        ("Software Engineer", "FutureSoft Systems", "Hyderabad", 5, 8, "Remote", "Full Time", "Entry Level", "Java, Spring, SQL", "AWS, Microservices", "Computer Science or IT", "Software Engineer", "Design and deliver scalable software for enterprise clients.", "Implement services, maintain CI/CD, solve technical defects.", "Software Developer"),
        ("Data Analyst", "DataForge Labs", "Pune", 4, 7, "Hybrid", "Full Time", "Entry Level", "SQL, Excel, Python, Data Analytics", "Power BI, Tableau", "B.Sc/B.Tech in Statistics or IT", "Data Analyst", "Analyze business data and produce actionable insight reports.", "Prepare dashboards, clean data and support business decision-making.", "Data Analyst"),
        ("Database Administrator", "InsightIQ Analytics", "Bangalore", 5, 9, "On-site", "Full Time", "Entry Level", "MySQL, SQL, Database Administration", "MongoDB, PostgreSQL", "B.Tech/IT/Computer Science", "Database Administrator", "Support database health, backup, and query optimization.", "Manage schema updates, monitor performance and backups.", "Database Administrator"),
        ("Cyber Security Analyst", "SecureGrid Systems", "Mumbai", 4, 7, "Hybrid", "Full Time", "Entry Level", "Cyber Security, Python, SQL", "Networking, SIEM", "B.E/B.Tech in CSE or related", "Cyber Security Analyst", "Monitor threat signals and support security operations.", "Assist with vulnerability testing and patch review.", "Cyber Security Analyst"),
        ("Frontend Developer", "PixelForge Studio", "Coimbatore", 3, 5, "On-site", "Full Time", "Fresher", "React, JavaScript, HTML, CSS", "TypeScript, UI/UX", "Any IT degree", "Frontend Developer", "Build marketing websites and customer dashboards.", "Translate design systems into responsive, accessible web interfaces.", "Frontend Developer"),
        ("Backend Developer", "CloudHive Solutions", "Delhi", 5, 8, "Remote", "Full Time", "Entry Level", "Node.js, Java, REST APIs", "AWS, Docker", "B.E/B.Tech/IT", "Backend Developer", "Support API infrastructure for SaaS products.", "Create services and data integrations with a focus on reliability.", "Backend Developer"),
        ("Full Stack Developer", "NovaStack Digital", "Remote", 4, 8, "Remote", "Full Time", "Entry Level", "JavaScript, React, Node.js, SQL", "MongoDB, Docker", "B.Tech/IT”, etc", "Full Stack Developer", "Build and maintain high-quality web applications.", "Work across front-end, back-end and database layers.", "Full Stack Developer"),
        ("AI/ML Engineer", "TechNova Solutions", "Bangalore", 6, 10, "Hybrid", "Full Time", "Entry Level", "Python, Machine Learning, SQL", "TensorFlow, NLP", "B.Tech/Computer Science", "AI/ML Engineer", "Develop machine learning models and analyze product data.", "Build data pipelines and evaluate predictive models.", "AI/ML Engineer"),
        ("Java Developer", "FutureSoft Systems", "Hyderabad", 4, 7, "On-site", "Full Time", "Entry Level", "Java, Spring, SQL", "Microservices, Apache Kafka", "B.E/B.Tech in Computer Science", "Java Developer", "Build backend services for enterprise workflows.", "Write clean code and support scalable service design.", "Java Developer"),
        ("Python Developer", "DataForge Labs", "Pune", 4, 6, "Hybrid", "Full Time", "Entry Level", "Python, SQL, Flask", "Machine Learning, Pandas", "B.Tech/IT/CS", "Python Developer", "Develop data applications and automations for business teams.", "Build logical scripts and backend integrations.", "Python Developer"),
        ("Software Engineer", "CodeSphere Technologies", "Chennai", 3, 6, "On-site", "Full Time", "Fresher", "JavaScript, Python, SQL", "Agile, Git", "B.E/B.Tech in CSE", "Software Engineer", "Build user-facing and internal platform solutions.", "Support engineering teams in improving product stability.", "Software Developer"),
        ("Data Analyst", "InsightIQ Analytics", "Bangalore", 3, 5, "Hybrid", "Full Time", "Fresher", "Data Analytics, SQL, Python", "Tableau, Excel", "B.Sc/B.Tech relevant majors", "Data Analyst", "Interpret customer and product data to guide business strategy.", "Create dashboards and summarize trends for stakeholders.", "Data Analyst"),
        ("Cyber Security Analyst", "Aegis Security Labs", "Remote", 5, 8, "Remote", "Full Time", "Entry Level", "Cyber Security, Python, Networking", "SOC, Linux", "B.E/B.Tech or diploma in IT", "Cyber Security Analyst", "Assist in security monitoring, triaging alerts and forensic reviews.", "Perform assessments and support incident response.", "Cyber Security Analyst"),
        ("DevOps Engineer", "CloudHive Solutions", "Delhi", 6, 10, "Hybrid", "Full Time", "Entry Level", "AWS, Docker, Kubernetes, Linux", "Terraform, CI/CD", "B.Tech/IT/CS", "DevOps Engineer", "Deploy and maintain reliable cloud systems.", "Own automation pipelines and monitoring improvements.", "DevOps Engineer"),
        ("UI/UX Designer", "PixelForge Studio", "Coimbatore", 2, 4, "Hybrid", "Full Time", "Fresher", "UI/UX, Figma, HTML, CSS", "Design systems, prototyping", "Any design/IT degree", "UI/UX Designer", "Design clean interfaces for digital products and websites.", "Collaborate with product and engineering teams.", "UI/UX Designer"),
        ("Mobile App Developer", "NovaStack Digital", "Remote", 5, 8, "Remote", "Full Time", "Entry Level", "Flutter, React Native, JavaScript", "API integration, Firebase", "B.Tech/IT or equivalent", "Mobile App Developer", "Develop mobile experiences for digital products.", "Work with backend and product teams to ship polished apps.", "Mobile App Developer"),
        ("Cloud Engineer", "TechNova Solutions", "Bangalore", 4, 7, "Hybrid", "Full Time", "Entry Level", "AWS, Azure, Cloud", "Terraform, DevOps", "B.Tech/IT/CS", "Cloud Engineer", "Support cloud environments for modern enterprise products.", "Deploy infrastructure and monitor platform health.", "Cloud Engineer"),
        ("Web Developer", "CodeSphere Technologies", "Chennai", 3, 5, "On-site", "Internship", "Fresher", "HTML, CSS, JavaScript, Python", "Bootstrap, SEO", "Any degree with interest in web", "Web Developer", "Develop website components and landing pages for projects.", "Improve speed and responsiveness across user journeys.", "Web Developer"),
        ("Python Developer", "FutureSoft Systems", "Hyderabad", 3, 6, "Remote", "Full Time", "Entry Level", "Python, Django, Flask", "APIs, SQL", "B.Tech/Computer Science", "Python Developer", "Create backend modules and improve process automation.", "Contribute to business systems using Python frameworks.", "Python Developer"),
        ("Full Stack Developer", "TechNova Solutions", "Bangalore", 6, 10, "Hybrid", "Full Time", "Entry Level", "React, Node.js, JavaScript, SQL", "REST APIs, AWS", "B.E/B.Tech/IT", "Full Stack Developer", "Deliver end-to-end product features for internal and customer tools.", "Work on both frontend and APIs with quality checks.", "Full Stack Developer"),
        ("Software Engineer", "SecureGrid Systems", "Mumbai", 4, 7, "Hybrid", "Full Time", "Entry Level", "C++, Java, Python", "Security tools, Git", "B.Tech/CS/IT", "Software Engineer", "Develop secure software and test product reliability.", "Collaborate with QA and operations teams on system improvements.", "Software Developer"),
        ("Java Developer", "DataForge Labs", "Pune", 3, 6, "On-site", "Full Time", "Fresher", "Java, Spring, SQL", "REST APIs, MySQL", "B.Tech/CS/IT", "Java Developer", "Create backend logic and support enterprise services.", "Work closely with product and QA teams to deliver clean features.", "Java Developer"),
        ("Data Analyst", "CloudHive Solutions", "Delhi", 4, 7, "Hybrid", "Full Time", "Entry Level", "SQL, Python, Data Analytics", "Power BI, Tableau", "B.Sc/B.Tech related field", "Data Analyst", "Model enterprise data pipelines and support KPI reporting.", "Extract insights from usage data for customers and operations.", "Data Analyst"),
        ("Database Administrator", "Aegis Security Labs", "Remote", 5, 8, "Remote", "Full Time", "Entry Level", "MySQL, SQL, Database Administration", "PostgreSQL, Linux", "B.E/B.Tech/IT", "Database Administrator", "Monitor and improve data architecture and reliability.", "Support backups, upgrades and database incident handling.", "Database Administrator"),
        ("Frontend Developer", "NovaStack Digital", "Remote", 2, 4, "Remote", "Internship", "Fresher", "JavaScript, HTML, CSS, React", "Accessibility, Figma", "Ongoing degree or diploma", "Frontend Developer", "Assist in designing responsive UI for web apps and dashboards.", "Implement pixel-perfect screens with high UX quality.", "Frontend Developer"),
        ("AI/ML Engineer", "InsightIQ Analytics", "Bangalore", 5, 8, "Hybrid", "Full Time", "Entry Level", "Python, Machine Learning, SQL", "NLP, Scikit-learn", "B.Tech/IT/CS", "AI/ML Engineer", "Work on prediction models and data labeling systems.", "Help build AI-driven insights for product teams.", "AI/ML Engineer"),
        ("DevOps Engineer", "Aegis Security Labs", "Remote", 5, 9, "Remote", "Full Time", "Entry Level", "Docker, Linux, AWS", "Terraform, CI/CD", "B.Tech/IT/CS", "DevOps Engineer", "Improve deployment reliability and automation processes.", "Create monitoring and automation scripts for product systems.", "DevOps Engineer"),
        ("Web Developer", "TechNova Solutions", "Bangalore", 2, 4, "On-site", "Internship", "Fresher", "HTML, CSS, JavaScript, React", "Git, UI/UX", "IT or design-related degree", "Web Developer", "Develop and maintain internal tools and customer portals.", "Support product team in building responsive interfaces.", "Web Developer"),
        ("Cyber Security Analyst", "FutureSoft Systems", "Hyderabad", 4, 7, "Hybrid", "Full Time", "Entry Level", "Cyber Security, Network Security, Python", "Linux, SIEM", "B.Tech/CS/IT", "Cyber Security Analyst", "Participate in threat monitoring and security audits.", "Investigate incidents and support secure system operations.", "Cyber Security Analyst"),
        ("Python Developer", "CodeSphere Technologies", "Chennai", 3, 6, "Remote", "Full Time", "Fresher", "Python, Flask, SQLite, JavaScript", "Django, REST APIs", "B.Tech/CS", "Python Developer", "Build and optimize CRUD systems and APIs.", "Write maintainable backend modules and support client features.", "Python Developer"),
    ]

    for title, company_name, location, salary_min, salary_max, workplace, employment, experience, required_skills, preferred_skills, education, job_title, description, responsibilities, category in job_entries:
        company_id = company_ids.get(company_name)
        if not company_id:
            continue
        conn.execute(
            "INSERT INTO jobs (company_id, recruiter_id, title, description, responsibilities, required_skills, preferred_skills, location, salary_min, salary_max, employment_type, workplace_type, experience_level, education, deadline, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', datetime('now', '-14 days'))",
            (
                company_id,
                recruiter_id,
                title,
                description,
                responsibilities,
                required_skills,
                preferred_skills,
                location,
                salary_min,
                salary_max,
                employment,
                workplace,
                experience,
                education,
                "2026-12-31",
            ),
        )

    conn.execute(
        "INSERT INTO saved_jobs (user_id, job_id) VALUES (?, (SELECT id FROM jobs WHERE title = 'Python Developer' LIMIT 1))",
        (josewa_id,),
    )
    conn.execute(
        "INSERT INTO saved_jobs (user_id, job_id) VALUES (?, (SELECT id FROM jobs WHERE title = 'Web Developer' LIMIT 1))",
        (josewa_id,),
    )
    conn.execute(
        "INSERT INTO saved_jobs (user_id, job_id) VALUES (?, (SELECT id FROM jobs WHERE title = 'Software Engineer' LIMIT 1))",
        (josewa_id,),
    )

    sample_job_id = conn.execute("SELECT id FROM jobs WHERE title = 'Python Developer' ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    resume_id = conn.execute("SELECT id FROM resumes WHERE user_id = ? LIMIT 1", (josewa_id,)).fetchone()[0]
    conn.execute(
        "INSERT INTO applications (user_id, job_id, resume_id, cover_letter, portfolio_url, github_url, linkedin_url, additional_info, status, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'shortlisted', datetime('now', '-3 days'))",
        (
            josewa_id,
            sample_job_id,
            resume_id,
            "Dear Hiring Team, I am enthusiastic about contributing my Python and web development knowledge to your team.",
            "https://portfolio.demo/josewa",
            "https://github.com/josewa-demo",
            "https://linkedin.com/in/josewa-demo",
            "I am excited to learn and contribute in a growth-focused team.",
        ),
    )
    app_id = conn.execute("SELECT id FROM applications WHERE user_id = ? ORDER BY applied_at DESC LIMIT 1", (josewa_id,)).fetchone()[0]
    conn.execute(
        "INSERT INTO interviews (application_id, interview_type, interview_date, interview_time, duration, interviewer, meeting_link, instructions, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')",
        (
            app_id,
            "Technical Interview",
            "2026-09-15",
            "10:30",
            "60 minutes",
            "Ritika Sharma",
            "https://meet.demo/jobconnect/technical-interview",
            "Please join with your camera on and be prepared to explain your projects and problem-solving approach.",
        ),
    )

    conn.execute(
        "INSERT INTO notifications (user_id, title, message, is_read) VALUES (?, ?, ?, 0)",
        (josewa_id, "Application shortlisted", "Your application for Python Developer was shortlisted for the next interview round.",),
    )
    conn.execute(
        "INSERT INTO notifications (user_id, title, message, is_read) VALUES (?, ?, ?, 0)",
        (josewa_id, "New job match", "Python Developer role in Bangalore matches your profile and skills (92% match).",),
    )
    conn.execute(
        "INSERT INTO notifications (user_id, title, message, is_read) VALUES (?, ?, ?, 1)",
        (josewa_id, "Application submitted", "Your resume and cover letter were submitted successfully for the Python Developer role.",),
    )

    conn.execute(
        "INSERT INTO job_alerts (user_id, keywords, location, employment_type, experience_level, active) VALUES (?, ?, ?, ?, ?, 1)",
        (josewa_id, "Python Developer", "Bangalore", "Full Time", "Entry Level"),
    )
    conn.execute(
        "INSERT INTO job_alerts (user_id, keywords, location, employment_type, experience_level, active) VALUES (?, ?, ?, ?, ?, 1)",
        (josewa_id, "Web Developer", "Chennai", "Full Time", "Fresher"),
    )

    conn.commit()
    conn.close()


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        data = request.form
        required = ["full_name", "username", "email", "phone", "password", "confirm_password", "location", "user_type"]
        for field in required:
            if not data.get(field, "").strip():
                flash("Please fill in all required fields.", "error")
                return render_template("register.html")

        full_name = data["full_name"].strip()
        username = data["username"].strip()
        email = data["email"].strip().lower()
        phone = data["phone"].strip()
        password = data["password"]
        confirm_password = data["confirm_password"]
        location = data["location"].strip()
        user_type = data["user_type"]

        if user_type not in ["job_seeker", "recruiter"]:
            flash("Invalid user type selected.", "error")
            return render_template("register.html")

        if "@" not in email or "." not in email:
            flash("Please enter a valid email address.", "error")
            return render_template("register.html")

        if len(password) < 8 or not any(ch.isupper() for ch in password) or not any(ch.isdigit() for ch in password):
            flash("Password must contain at least 8 characters, one uppercase letter, and one number.", "error")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        conn = get_db_connection()
        if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            flash("A user with this email already exists.", "error")
            conn.close(); return render_template("register.html")
        if conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            flash("This username is already taken.", "error")
            conn.close(); return render_template("register.html")

        hashed = generate_password_hash(password)
        cursor = conn.execute(
            "INSERT INTO users (username, full_name, email, phone, password_hash, role, location) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, full_name, email, phone, hashed, user_type, location),
        )
        user_id = cursor.lastrowid
        conn.execute("INSERT INTO profiles (user_id, headline, about, location) VALUES (?, ?, ?, ?)", (user_id, "Professional profile", "New profile created.", location))
        conn.commit(); conn.close()
        session["user_id"] = user_id
        flash("Account created successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        if not identifier or not password:
            flash("Enter both username/email and password.", "error")
            return render_template("login.html")

        conn = get_db_connection()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (identifier, identifier),
        ).fetchone()
        conn.close()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid username/email or password.", "error")
            return render_template("login.html")

        session["user_id"] = user["id"]
        flash("Welcome back!", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_current_user()
    if user["role"] == "job_seeker":
        profile_completion = compute_profile_completion(user["id"])
        recommended = get_skill_matches(user["id"])
        saved_jobs = query_jobs()
        saved_count = 0
        if user["id"]:
            conn = get_db_connection()
            saved_count = conn.execute("SELECT COUNT(*) FROM saved_jobs WHERE user_id = ?", (user["id"],)).fetchone()[0]
            app_count = conn.execute("SELECT COUNT(*) FROM applications WHERE user_id = ?", (user["id"],)).fetchone()[0]
            interview_count = conn.execute(
                "SELECT COUNT(*) FROM interviews i JOIN applications a ON a.id = i.application_id WHERE a.user_id = ?",
                (user["id"],),
            ).fetchone()[0]
            notification_count = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0", (user["id"],)).fetchone()[0]
            conn.close()
        return render_template(
            "dashboard.html",
            user=user,
            profile_completion=profile_completion,
            recommended_jobs=recommended[:4],
            saved_jobs_count=saved_count,
            applications_count=app_count,
            upcoming_interviews_count=interview_count,
            unread_notifications_count=notification_count,
            resumes_count=0,
        )
    if user["role"] == "recruiter":
        conn = get_db_connection()
        active_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE recruiter_id = ? AND status = 'active'", (user["id"],)).fetchone()[0]
        applicants = conn.execute("SELECT COUNT(*) FROM applications a JOIN jobs j ON j.id = a.job_id WHERE j.recruiter_id = ?", (user["id"],)).fetchone()[0]
        shortlisted = conn.execute("SELECT COUNT(*) FROM applications a JOIN jobs j ON j.id = a.job_id WHERE j.recruiter_id = ? AND a.status = 'shortlisted'", (user["id"],)).fetchone()[0]
        interviews = conn.execute("SELECT COUNT(*) FROM interviews i JOIN applications a ON a.id = i.application_id JOIN jobs j ON j.id = a.job_id WHERE j.recruiter_id = ?", (user["id"],)).fetchone()[0]
        selected = conn.execute("SELECT COUNT(*) FROM applications a JOIN jobs j ON j.id = a.job_id WHERE j.recruiter_id = ? AND a.status = 'selected'", (user["id"],)).fetchone()[0]
        conn.close()
        return render_template(
            "recruiter_dashboard.html",
            user=user,
            active_jobs=active_jobs,
            applicants=applicants,
            shortlisted=shortlisted,
            interviews=interviews,
            selected=selected,
        )

    conn = get_db_connection()
    stats = {
        "total_users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "job_seekers": conn.execute("SELECT COUNT(*) FROM users WHERE role = 'job_seeker'").fetchone()[0],
        "recruiters": conn.execute("SELECT COUNT(*) FROM users WHERE role = 'recruiter'").fetchone()[0],
        "companies": conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
        "jobs": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "applications": conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
        "interviews": conn.execute("SELECT COUNT(*) FROM interviews").fetchone()[0],
        "selected": conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'selected'").fetchone()[0],
    }
    conn.close()
    return render_template("admin_dashboard.html", user=user, stats=stats)


@app.route("/jobs")
def jobs():
    filters = {
        "location": request.args.get("location"),
        "employment_type": request.args.get("employment_type"),
        "experience_level": request.args.get("experience_level"),
        "workplace_type": request.args.get("workplace_type"),
        "min_salary": request.args.get("min_salary"),
        "max_salary": request.args.get("max_salary"),
        "date_range": request.args.get("date_range"),
        "skills": request.args.get("skills"),
    }
    q = request.args.get("q")
    jobs_list = query_jobs(filters, q)
    user = get_current_user()
    for job in jobs_list:
        job["is_saved"] = is_saved_by_user(user["id"] if user else None, job["id"])
    return render_template("jobs.html", jobs=jobs_list, q=q, filters=filters, user=user)


@app.route("/job/<int:job_id>")
def job_detail(job_id):
    job = get_job_details(job_id)
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("jobs"))
    user = get_current_user()
    job["is_saved"] = is_saved_by_user(user["id"] if user else None, job_id)
    return render_template("job_detail.html", job=job, user=user)


@app.route("/save_job/<int:job_id>", methods=["POST"])
@login_required
def save_job(job_id):
    conn = get_db_connection()
    job = conn.execute("SELECT id FROM jobs WHERE id = ? AND status = 'active'", (job_id,)).fetchone()
    if not job:
        conn.close()
        flash("Job not found.", "error")
        return redirect(request.referrer or url_for("jobs"))
    existing = conn.execute("SELECT id FROM saved_jobs WHERE user_id = ? AND job_id = ?", (session["user_id"], job_id)).fetchone()
    if not existing:
        conn.execute("INSERT INTO saved_jobs (user_id, job_id) VALUES (?, ?)", (session["user_id"], job_id))
        conn.commit()
        add_notification(session["user_id"], "Job saved", "The job was saved to your list.")
        flash("Job saved successfully.", "success")
    else:
        flash("This job is already in your saved list.", "info")
    conn.close()
    return redirect(request.referrer or url_for("jobs"))


@app.route("/saved_jobs")
@login_required
def saved_jobs():
    user = get_current_user()
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT sj.id AS saved_job_id, sj.saved_at, j.id AS job_id, j.*, c.company_name,
               (SELECT COUNT(*) FROM applications a WHERE a.job_id = j.id) AS applicant_count
        FROM saved_jobs sj
        JOIN jobs j ON j.id = sj.job_id
        LEFT JOIN companies c ON c.id = j.company_id
        WHERE sj.user_id = ?
        ORDER BY sj.saved_at DESC
        """,
        (user["id"],),
    ).fetchall()
    conn.close()
    jobs = [dict(row) for row in rows]
    return render_template("saved_jobs.html", jobs=jobs, user=user)


@app.route("/remove_saved_job/<int:saved_job_id>", methods=["POST"])
@login_required
def remove_saved_job(saved_job_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM saved_jobs WHERE id = ? AND user_id = ?", (saved_job_id, session["user_id"]))
    conn.commit(); conn.close()
    flash("Job removed from saved jobs.", "success")
    return redirect(url_for("saved_jobs"))


@app.route("/apply/<int:job_id>", methods=["GET", "POST"])
@login_required
def apply_job(job_id):
    if get_current_user()["role"] != "job_seeker":
        flash("Only job seekers can apply for jobs.", "error")
        return redirect(url_for("dashboard"))

    job = get_job_details(job_id)
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("jobs"))

    if request.method == "POST":
        user = get_current_user()
        conn = get_db_connection()
        existing = conn.execute("SELECT id FROM applications WHERE user_id = ? AND job_id = ?", (user["id"], job_id)).fetchone()
        if existing:
            flash("You have already submitted an application for this job.", "error")
            conn.close()
            return redirect(url_for("jobs"))

        file = request.files.get("resume")
        resume_id = None
        if file and file.filename:
            filename = secure_filename(file.filename)
            ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
            if ext not in ALLOWED_EXTENSIONS:
                flash("Resume must be PDF, DOC, or DOCX.", "error"); conn.close(); return render_template("apply.html", job=job, user=user)
            if file.content_length and file.content_length > app.config["MAX_CONTENT_LENGTH"]:
                flash("Resume file is too large. Max allowed size is 5 MB.", "error"); conn.close(); return render_template("apply.html", job=job, user=user)
            unique_name = f"{uuid.uuid4().hex}_{filename}"
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
            file.save(save_path)
            resume_id = conn.execute(
                "INSERT INTO resumes (user_id, file_name, file_path, file_type, file_size, uploaded_at, is_default) VALUES (?, ?, ?, ?, ?, datetime('now'), 0)",
                (user["id"], filename, save_path, f"application/{ext}", os.path.getsize(save_path)),
            ).lastrowid
        else:
            resume_default = get_default_resume(user["id"])
            if resume_default:
                resume_id = resume_default["id"]

        if not resume_id:
            flash("Please upload a resume or select a default resume before applying.", "error")
            conn.close()
            return render_template("apply.html", job=job, user=user)

        cover_letter = request.form.get("cover_letter", "")
        portfolio_url = request.form.get("portfolio_url", "")
        github_url = request.form.get("github_url", "")
        linkedin_url = request.form.get("linkedin_url", "")
        additional_info = request.form.get("additional_info", "")

        cursor = conn.execute(
            "INSERT INTO applications (user_id, job_id, resume_id, cover_letter, portfolio_url, github_url, linkedin_url, additional_info, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'applied')",
            (user["id"], job_id, resume_id, cover_letter, portfolio_url, github_url, linkedin_url, additional_info),
        )
        application_id = cursor.lastrowid
        conn.commit(); conn.close()
        add_notification(user["id"], "Application submitted", f"Your application for {job['title']} was submitted successfully.")
        flash("Application submitted successfully! Application ID: JOB-" + str(application_id), "success")
        return redirect(url_for("application_detail", application_id=application_id))

    user = get_current_user()
    return render_template("apply.html", job=job, user=user)


@app.route("/applications")
@login_required
def applications_page():
    user = get_current_user()
    view = request.args.get("view", "list")
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT a.*, j.title AS job_title, c.company_name, r.file_name AS resume_name,
        (SELECT COUNT(*) FROM interviews i WHERE i.application_id = a.id) AS interview_count
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        LEFT JOIN companies c ON c.id = j.company_id
        LEFT JOIN resumes r ON r.id = a.resume_id
        WHERE a.user_id = ?
        ORDER BY a.applied_at DESC
        """,
        (user["id"],),
    ).fetchall()
    conn.close()
    apps = [dict(row) for row in rows]
    return render_template("applications.html", applications=apps, user=user, view=view)


@app.route("/application/<int:application_id>")
@login_required
def application_detail(application_id):
    user = get_current_user()
    conn = get_db_connection()
    app_row = conn.execute(
        """
        SELECT a.*, j.title AS job_title, j.location AS job_location, j.recruiter_id, c.company_name, u.full_name AS recruiter_name,
               r.file_name AS resume_name
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        LEFT JOIN companies c ON c.id = j.company_id
        LEFT JOIN users u ON u.id = j.recruiter_id
        LEFT JOIN resumes r ON r.id = a.resume_id
        WHERE a.id = ?
        """,
        (application_id,),
    ).fetchone()
    if not app_row:
        conn.close(); flash("Application not found.", "error"); return redirect(url_for("applications_page"))
    recruiter_can_view = user["role"] == "recruiter" and app_row["recruiter_id"] == user["id"]
    if app_row["user_id"] != user["id"] and user["role"] != "admin" and not recruiter_can_view:
        conn.close(); flash("Unauthorized access.", "error"); return redirect(url_for("dashboard"))
    timeline = [
        "Application Submitted",
        "Application Reviewed",
        "Shortlisted",
        "Interview Scheduled",
        "Final Decision",
    ]
    conn.close()
    return render_template("application_detail.html", application=dict(app_row), user=user, timeline=timeline)


@app.route("/resume", methods=["GET", "POST"])
def resume_management():
    user = get_current_user()
    if not user:
        flash("Please log in to manage resumes.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        file = request.files.get("resume")
        if not file or not file.filename:
            flash("Choose a resume file to upload.", "error")
            return redirect(url_for("resume_management"))
        filename = secure_filename(file.filename)
        ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            flash("Invalid resume type. Allowed: PDF, DOC, DOCX.", "error")
            return redirect(url_for("resume_management"))
        if file.content_length and file.content_length > app.config["MAX_CONTENT_LENGTH"]:
            flash("Resume is too large. Maximum size is 5 MB.", "error")
            return redirect(url_for("resume_management"))
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
        file.save(save_path)
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO resumes (user_id, file_name, file_path, file_type, file_size, uploaded_at, is_default) VALUES (?, ?, ?, ?, ?, datetime('now'), 0)",
            (user["id"], filename, save_path, f"application/{ext}", os.path.getsize(save_path)),
        )
        conn.commit(); conn.close()
        flash("Resume uploaded successfully.", "success")
        return redirect(url_for("resume_management"))

    conn = get_db_connection()
    resumes = conn.execute("SELECT * FROM resumes WHERE user_id = ? ORDER BY uploaded_at DESC", (user["id"],)).fetchall()
    conn.close()
    return render_template("resume.html", resumes=[dict(row) for row in resumes], user=user)


@app.route("/resume/download/<int:resume_id>")
@login_required
def download_resume(resume_id):
    user = get_current_user()
    conn = get_db_connection()
    resume = conn.execute(
        """
        SELECT r.*,
               EXISTS(
                   SELECT 1
                   FROM applications a
                   JOIN jobs j ON j.id = a.job_id
                   WHERE a.resume_id = ? AND j.recruiter_id = ?
               ) AS recruiter_access
        FROM resumes r
        WHERE r.id = ?
        """,
        (resume_id, user["id"], resume_id),
    ).fetchone()
    conn.close()
    can_download = resume and (
        resume["user_id"] == user["id"]
        or user["role"] == "admin"
        or (user["role"] == "recruiter" and resume["recruiter_access"])
    )
    if not can_download:
        flash("You do not have permission to access this resume.", "error")
        return redirect(url_for("resume_management"))
    file_path = resolve_resume_path(resume["file_path"])
    if not file_path.is_file():
        flash("Resume file not found.", "error")
        return redirect(url_for("resume_management"))
    return send_file(file_path, as_attachment=True, download_name=resume["file_name"])


@app.route("/resume/delete/<int:resume_id>", methods=["POST"])
@login_required
def delete_resume(resume_id):
    user = get_current_user()
    conn = get_db_connection()
    resume = conn.execute("SELECT * FROM resumes WHERE id = ? AND user_id = ?", (resume_id, user["id"])).fetchone()
    if resume:
        resume_path = resolve_resume_path(resume["file_path"])
        if resume_path.is_file():
            resume_path.unlink()
        conn.execute("DELETE FROM resumes WHERE id = ? AND user_id = ?", (resume_id, user["id"]))
        conn.commit()
        flash("Resume deleted successfully.", "success")
    else:
        flash("Resume not found.", "error")
    conn.close(); return redirect(url_for("resume_management"))


@app.route("/resume/default/<int:resume_id>", methods=["POST"])
@login_required
def set_default_resume(resume_id):
    user = get_current_user()
    conn = get_db_connection()
    conn.execute("UPDATE resumes SET is_default = 0 WHERE user_id = ?", (user["id"],))
    updated = conn.execute("UPDATE resumes SET is_default = 1 WHERE id = ? AND user_id = ?", (resume_id, user["id"]))
    conn.commit(); conn.close()
    flash("Default resume updated." if updated.rowcount else "Resume not found.", "success" if updated.rowcount else "error")
    return redirect(url_for("resume_management"))


@app.route("/profile", methods=["GET", "POST"])
def profile():
    user = get_current_user()
    if not user:
        flash("Please log in to view your profile.", "error")
        return redirect(url_for("login"))

    conn = get_db_connection()
    profile_data = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user["id"],)).fetchone()
    skills = conn.execute(
        "SELECT s.name, us.proficiency FROM user_skills us JOIN skills s ON s.id = us.skill_id WHERE us.user_id = ? ORDER BY s.name",
        (user["id"],),
    ).fetchall()
    education = conn.execute("SELECT * FROM education WHERE user_id = ? ORDER BY end_year DESC", (user["id"],)).fetchall()
    experiences = conn.execute("SELECT * FROM experience WHERE user_id = ? ORDER BY end_date DESC", (user["id"],)).fetchall()
    projects = conn.execute("SELECT * FROM projects WHERE user_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    certifications = conn.execute("SELECT * FROM certifications WHERE user_id = ? ORDER BY issue_date DESC", (user["id"],)).fetchall()
    conn.close()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_basic":
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            location = request.form.get("location", "").strip()
            headline = request.form.get("headline", "").strip()
            conn = get_db_connection()
            conn.execute(
                "UPDATE users SET full_name = ?, email = ?, phone = ?, location = ? WHERE id = ?",
                (full_name, email, phone, location, user["id"]),
            )
            conn.execute(
                "INSERT INTO profiles (user_id, headline, about, location) VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET headline = excluded.headline, about = profiles.about, location = excluded.location",
                (user["id"], headline, profile_data["about"] if profile_data else "", location),
            )
            conn.commit(); conn.close();
            compute_profile_completion(user["id"])
            flash("Profile information updated.", "success")
            return redirect(url_for("profile"))
        if action == "update_about":
            about = request.form.get("about", "").strip()
            conn = get_db_connection()
            conn.execute(
                "INSERT INTO profiles (user_id, headline, about, location) VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET about = excluded.about",
                (user["id"], profile_data["headline"] if profile_data else "", about, profile_data["location"] if profile_data else user["location"]),
            )
            conn.commit(); conn.close();
            compute_profile_completion(user["id"])
            flash("About section updated.", "success")
            return redirect(url_for("profile"))
        if action == "add_skill":
            skill_name = request.form.get("skill_name", "").strip()
            proficiency = request.form.get("proficiency", "Intermediate")
            if skill_name:
                conn = get_db_connection()
                conn.execute("INSERT OR IGNORE INTO skills (name) VALUES (?)", (skill_name,))
                skill = conn.execute("SELECT id FROM skills WHERE name = ?", (skill_name,)).fetchone()
                if skill:
                    exists = conn.execute("SELECT id FROM user_skills WHERE user_id = ? AND skill_id = ?", (user["id"], skill["id"])).fetchone()
                    if not exists:
                        conn.execute("INSERT INTO user_skills (user_id, skill_id, proficiency) VALUES (?, ?, ?)", (user["id"], skill["id"], proficiency))
                conn.commit(); conn.close();
                compute_profile_completion(user["id"])
                flash("Skill added.", "success")
            return redirect(url_for("profile"))
        if action == "add_education":
            institution = request.form.get("institution", "").strip()
            degree = request.form.get("degree", "").strip()
            field = request.form.get("field", "").strip()
            start_year = request.form.get("start_year", "")
            end_year = request.form.get("end_year", "")
            grade = request.form.get("grade", "").strip()
            if institution:
                conn = get_db_connection()
                conn.execute(
                    "INSERT INTO education (user_id, institution, degree, field, start_year, end_year, grade) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user["id"], institution, degree, field, start_year, end_year, grade),
                )
                conn.commit(); conn.close();
                compute_profile_completion(user["id"])
                flash("Education entry added.", "success")
            return redirect(url_for("profile"))
        if action == "add_experience":
            company = request.form.get("company", "").strip()
            job_title = request.form.get("job_title", "").strip()
            start_date = request.form.get("start_date", "")
            end_date = request.form.get("end_date", "")
            description = request.form.get("description", "").strip()
            if company:
                conn = get_db_connection()
                conn.execute(
                    "INSERT INTO experience (user_id, company, job_title, start_date, end_date, description) VALUES (?, ?, ?, ?, ?, ?)",
                    (user["id"], company, job_title, start_date, end_date, description),
                )
                conn.commit(); conn.close();
                compute_profile_completion(user["id"])
                flash("Experience entry added.", "success")
            return redirect(url_for("profile"))
        if action == "add_project":
            project_name = request.form.get("project_name", "").strip()
            description = request.form.get("description", "").strip()
            technologies = request.form.get("technologies", "").strip()
            github_url = request.form.get("github_url", "").strip()
            demo_url = request.form.get("demo_url", "").strip()
            if project_name:
                conn = get_db_connection()
                conn.execute(
                    "INSERT INTO projects (user_id, project_name, description, technologies, github_url, demo_url) VALUES (?, ?, ?, ?, ?, ?)",
                    (user["id"], project_name, description, technologies, github_url, demo_url),
                )
                conn.commit(); conn.close();
                compute_profile_completion(user["id"])
                flash("Project added.", "success")
            return redirect(url_for("profile"))
        if action == "add_certification":
            cert_name = request.form.get("certificate_name", "").strip()
            organization = request.form.get("organization", "").strip()
            issue_date = request.form.get("issue_date", "")
            credential_id = request.form.get("credential_id", "").strip()
            if cert_name:
                conn = get_db_connection()
                conn.execute(
                    "INSERT INTO certifications (user_id, certificate_name, organization, issue_date, credential_id) VALUES (?, ?, ?, ?, ?)",
                    (user["id"], cert_name, organization, issue_date, credential_id),
                )
                conn.commit(); conn.close();
                flash("Certification added.", "success")
            return redirect(url_for("profile"))

    profile_completion = compute_profile_completion(user["id"])
    return render_template(
        "profile.html",
        user=user,
        profile_data=profile_data,
        profile_completion=profile_completion,
        skills=skills,
        education=education,
        experiences=experiences,
        projects=projects,
        certifications=certifications,
    )


@app.route("/interviews")
@login_required
def interviews_page():
    user = get_current_user()
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT i.*, a.id AS application_id, j.title AS job_title, c.company_name, a.status AS application_status
        FROM interviews i
        JOIN applications a ON a.id = i.application_id
        JOIN jobs j ON j.id = a.job_id
        LEFT JOIN companies c ON c.id = j.company_id
        WHERE a.user_id = ?
        ORDER BY i.interview_date ASC
        """,
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template("interviews.html", interviews=[dict(row) for row in rows], user=user)


@app.route("/interview/<int:interview_id>")
@login_required
def interview_detail(interview_id):
    user = get_current_user()
    conn = get_db_connection()
    interview = conn.execute(
        """
        SELECT i.*, a.user_id, j.recruiter_id, j.title AS job_title, c.company_name
        FROM interviews i
        JOIN applications a ON a.id = i.application_id
        JOIN jobs j ON j.id = a.job_id
        LEFT JOIN companies c ON c.id = j.company_id
        WHERE i.id = ?
        """,
        (interview_id,),
    ).fetchone()
    conn.close()
    allowed = interview and (
        interview["user_id"] == user["id"]
        or user["role"] == "admin"
        or (user["role"] == "recruiter" and interview["recruiter_id"] == user["id"])
    )
    if not allowed:
        flash("Interview not found.", "error")
        return redirect(url_for("interviews_page"))
    return render_template("interview_detail.html", interview=dict(interview), user=user)


@app.route("/notifications")
@login_required
def notifications_page():
    user = get_current_user()
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)).fetchall()
    conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user["id"],))
    conn.commit(); conn.close()
    return render_template("notifications.html", notifications=[dict(row) for row in rows], user=user)


@app.route("/job_alerts", methods=["GET", "POST"])
def job_alerts():
    user = get_current_user()
    if not user:
        flash("Please log in to manage job alerts.", "error")
        return redirect(url_for("login"))
    if request.method == "POST":
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO job_alerts (user_id, keywords, location, employment_type, experience_level, active) VALUES (?, ?, ?, ?, ?, 1)",
            (
                user["id"],
                request.form.get("keywords", "").strip(),
                request.form.get("location", "").strip(),
                request.form.get("employment_type", "").strip(),
                request.form.get("experience_level", "").strip(),
            ),
        )
        conn.commit(); conn.close()
        flash("Job alert created successfully.", "success")
        return redirect(url_for("job_alerts"))

    conn = get_db_connection()
    alerts = conn.execute("SELECT * FROM job_alerts WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)).fetchall()
    conn.close()
    return render_template("job_alerts.html", alerts=[dict(row) for row in alerts], user=user)


@app.route("/recruiter/jobs")
@login_required
@role_required("recruiter")
def recruiter_jobs():
    user = get_current_user()
    conn = get_db_connection()
    jobs_list = conn.execute(
        "SELECT j.*, c.company_name, (SELECT COUNT(*) FROM applications a WHERE a.job_id = j.id) AS applicant_count FROM jobs j LEFT JOIN companies c ON c.id = j.company_id WHERE j.recruiter_id = ? ORDER BY j.created_at DESC",
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template("recruiter_jobs.html", jobs=[dict(row) for row in jobs_list], user=user)


@app.route("/recruiter/job/new", methods=["POST"])
@login_required
@role_required("recruiter")
def create_job():
    user = get_current_user()
    conn = get_db_connection()
    company_name = request.form.get("company_name", "").strip()
    title = request.form.get("title", "").strip()
    if not title:
        conn.close()
        flash("Job title is required.", "error")
        return redirect(url_for("recruiter_jobs"))
    try:
        salary_min = int(request.form.get("salary_min", 0) or 0)
        salary_max = int(request.form.get("salary_max", 0) or 0)
    except ValueError:
        conn.close()
        flash("Salary values must be whole numbers.", "error")
        return redirect(url_for("recruiter_jobs"))
    if salary_min < 0 or salary_max < 0 or salary_max < salary_min:
        conn.close()
        flash("Please enter a valid salary range.", "error")
        return redirect(url_for("recruiter_jobs"))
    company = conn.execute("SELECT * FROM companies WHERE recruiter_id = ? LIMIT 1", (user["id"],)).fetchone()
    if not company:
        # create default company if not present
        conn.execute(
            "INSERT INTO companies (recruiter_id, company_name, description, industry, location, website, employee_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user["id"], company_name or "Demo Company", "Demo company profile for the internship project.", "IT Services", user["location"], "https://demo.company", 100),
        )
        company = conn.execute("SELECT * FROM companies WHERE recruiter_id = ? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()

    conn.execute(
        "INSERT INTO jobs (company_id, recruiter_id, title, description, responsibilities, required_skills, preferred_skills, location, salary_min, salary_max, employment_type, workplace_type, experience_level, education, deadline, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
        (
            company["id"],
            user["id"],
            title,
            request.form.get("description", "").strip(),
            request.form.get("responsibilities", "").strip(),
            request.form.get("required_skills", "").strip(),
            request.form.get("preferred_skills", "").strip(),
            request.form.get("location", "").strip(),
            salary_min,
            salary_max,
            request.form.get("employment_type", "Full Time"),
            request.form.get("workplace_type", "Hybrid"),
            request.form.get("experience_level", "Entry Level"),
            request.form.get("education", "").strip(),
            request.form.get("deadline", "2026-12-31"),
        ),
    )
    conn.commit(); conn.close()
    flash("Job posting created successfully.", "success")
    return redirect(url_for("recruiter_jobs"))


@app.route("/recruiter/applicants")
@login_required
@role_required("recruiter")
def recruiter_applicants():
    user = get_current_user()
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT a.id, a.status, a.applied_at, j.title AS job_title, u.full_name, u.email, c.company_name,
               (SELECT COUNT(*) FROM interviews i WHERE i.application_id = a.id) AS interview_count,
               (SELECT file_name FROM resumes r WHERE r.id = a.resume_id) AS resume_name
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        LEFT JOIN companies c ON c.id = j.company_id
        JOIN users u ON u.id = a.user_id
        WHERE j.recruiter_id = ?
        ORDER BY a.applied_at DESC
        """,
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template("recruiter_applicants.html", applicants=[dict(row) for row in rows], user=user)


@app.route("/recruiter/application/<int:application_id>/status", methods=["POST"])
@login_required
@role_required("recruiter")
def update_application_status(application_id):
    user = get_current_user()
    status = request.form.get("status")
    if status not in {"applied", "under_review", "shortlisted", "interview", "selected", "rejected"}:
        flash("Invalid application status.", "error")
        return redirect(url_for("recruiter_applicants"))
    conn = get_db_connection()
    app = conn.execute("SELECT a.*, j.recruiter_id FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.id = ?", (application_id,)).fetchone()
    if not app or app["recruiter_id"] != user["id"]:
        conn.close(); flash("Application not found.", "error"); return redirect(url_for("recruiter_applicants"))
    conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, application_id))
    conn.commit(); conn.close()
    add_notification(app["user_id"], "Application status updated", f"Your application status is now {status}.")
    flash("Application status updated.", "success")
    return redirect(url_for("recruiter_applicants"))


@app.route("/recruiter/interview", methods=["POST"])
@login_required
@role_required("recruiter")
def schedule_interview():
    user = get_current_user()
    application_id = request.form.get("application_id")
    interview_type = request.form.get("interview_type")
    interview_date = request.form.get("interview_date")
    interview_time = request.form.get("interview_time")
    duration = request.form.get("duration")
    interviewer = request.form.get("interviewer")
    meeting_link = request.form.get("meeting_link")
    instructions = request.form.get("instructions")

    conn = get_db_connection()
    app = conn.execute("SELECT a.*, j.recruiter_id, j.title AS job_title, u.full_name AS user_name FROM applications a JOIN jobs j ON j.id = a.job_id JOIN users u ON u.id = a.user_id WHERE a.id = ?", (application_id,)).fetchone()
    if not app or app["recruiter_id"] != user["id"]:
        conn.close(); flash("Application not found.", "error"); return redirect(url_for("recruiter_applicants"))
    conn.execute(
        "INSERT INTO interviews (application_id, interview_type, interview_date, interview_time, duration, interviewer, meeting_link, instructions, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')",
        (application_id, interview_type, interview_date, interview_time, duration, interviewer, meeting_link or 'https://meet.demo/jobconnect', instructions),
    )
    conn.execute("UPDATE applications SET status = 'interview' WHERE id = ?", (application_id,))
    conn.commit(); conn.close()
    add_notification(app["user_id"], "Interview scheduled", f"You have a {interview_type} for {app['job_title']} on {interview_date} at {interview_time}.")
    flash("Interview scheduled successfully.", "success")
    return redirect(url_for("recruiter_applicants"))


@app.route("/admin/users")
@login_required
@role_required("admin")
def admin_users():
    conn = get_db_connection()
    users = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("admin_users.html", users=[dict(row) for row in users], user=get_current_user())


@app.route("/admin/jobs")
@login_required
@role_required("admin")
def admin_jobs():
    conn = get_db_connection()
    jobs_list = conn.execute("SELECT j.*, c.company_name FROM jobs j LEFT JOIN companies c ON c.id = j.company_id ORDER BY j.created_at DESC").fetchall()
    conn.close()
    return render_template("admin_jobs.html", jobs=[dict(row) for row in jobs_list], user=get_current_user())


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    conn = get_db_connection()
    jobs = conn.execute("SELECT id, title, location FROM jobs WHERE title LIKE ? LIMIT 5", (f"%{q}%",)).fetchall()
    companies = conn.execute("SELECT id, company_name FROM companies WHERE company_name LIKE ? LIMIT 5", (f"%{q}%",)).fetchall()
    skills = conn.execute("SELECT id, name FROM skills WHERE name LIKE ? LIMIT 5", (f"%{q}%",)).fetchall()
    conn.close()
    results = {
        "jobs": [{"id": row["id"], "label": row["title"], "meta": row["location"]} for row in jobs],
        "companies": [{"id": row["id"], "label": row["company_name"], "meta": "Company"} for row in companies],
        "skills": [{"id": row["id"], "label": row["name"], "meta": "Skill"} for row in skills],
    }
    return jsonify(results)


@app.route("/search")
def search_results():
    q = request.args.get("q", "")
    user = get_current_user()
    jobs_list = query_jobs(q=q)
    return render_template("search_results.html", jobs=jobs_list, query=q, user=user)


@app.before_request
def setup_demo_data():
    init_db()
    ensure_demo_data()


@app.context_processor
def inject_user():
    return {"current_user": get_current_user()}


if __name__ == "__main__":
    init_db()
    ensure_demo_data()
    app.run(debug=True, host="0.0.0.0", port=5000)

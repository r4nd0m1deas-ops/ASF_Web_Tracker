#!/usr/bin/env python3
"""ASF Maintenance Task Tracker - Web Version rev 1.00"""
import os, json, sqlite3, csv, io
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, flash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'asf-tracker-secret-change-me')

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'asf_tracker.db')

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE, password TEXT, full_name TEXT,
        is_admin INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, username TEXT, equipment TEXT,
        hours REAL, resolved INTEGER, has_wo INTEGER,
        wo_number TEXT, notes TEXT, submitted INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    )''')
    # Seed admin
    if not c.execute('SELECT id FROM users WHERE username=?', ('admin',)).fetchone():
        c.execute('INSERT INTO users (username, password, full_name, is_admin) VALUES (?,?,?,1)',
                  ('admin', 'admin123', 'Administrator'))
    if not c.execute('SELECT key FROM settings WHERE key=?', ('default_emails',)).fetchone():
        c.execute('INSERT INTO settings (key, value) VALUES (?,?)', ('default_emails', ''))
    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*a, **kw)
    return wrap

def admin_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if 'user' not in session or not session.get('is_admin'):
            return redirect(url_for('dashboard'))
        return f(*a, **kw)
    return wrap

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username','').strip()
        p = request.form.get('password','')
        conn = get_db()
        row = conn.execute('SELECT * FROM users WHERE username=? AND is_active=1', (u,)).fetchone()
        conn.close()
        if row and row['password'] == p:
            session['user'] = row['username']
            session['full_name'] = row['full_name'] or row['username']
            session['is_admin'] = bool(row['is_admin'])
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    tasks = conn.execute('SELECT * FROM tasks WHERE username=? AND date=? AND submitted=0 ORDER BY id',
                         (session['user'], today)).fetchall()
    total = sum(t['hours'] or 0 for t in tasks)
    emails = conn.execute('SELECT value FROM settings WHERE key=?', ('default_emails',)).fetchone()
    conn.close()
    return render_template('dashboard.html', tasks=tasks, total=total, today=today,
                           emails=(emails['value'] if emails else ''))

@app.route('/task/add', methods=['POST'])
@login_required
def task_add():
    today = datetime.now().strftime('%Y-%m-%d')
    hrs = float(request.form.get('hours') or 0)
    mins = float(request.form.get('minutes') or 0)
    decimal = hrs + mins/60.0
    conn = get_db()
    conn.execute('''INSERT INTO tasks (date, username, equipment, hours, resolved, has_wo, wo_number, notes)
                    VALUES (?,?,?,?,?,?,?,?)''',
                 (today, session['user'], request.form.get('equipment',''), decimal,
                  1 if request.form.get('resolved') else 0,
                  1 if request.form.get('has_wo') else 0,
                  request.form.get('wo_number','') if request.form.get('has_wo') else '',
                  (request.form.get('notes') or '')[:255]))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/task/edit/<int:tid>', methods=['GET','POST'])
@login_required
def task_edit(tid):
    conn = get_db()
    t = conn.execute('SELECT * FROM tasks WHERE id=? AND username=?', (tid, session['user'])).fetchone()
    if not t:
        conn.close()
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        hrs = float(request.form.get('hours') or 0)
        mins = float(request.form.get('minutes') or 0)
        decimal = hrs + mins/60.0
        conn.execute('''UPDATE tasks SET equipment=?, hours=?, resolved=?, has_wo=?, wo_number=?, notes=?
                        WHERE id=?''',
                     (request.form.get('equipment',''), decimal,
                      1 if request.form.get('resolved') else 0,
                      1 if request.form.get('has_wo') else 0,
                      request.form.get('wo_number','') if request.form.get('has_wo') else '',
                      (request.form.get('notes') or '')[:255], tid))
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard'))
    conn.close()
    return render_template('edit.html', task=t)

@app.route('/task/delete/<int:tid>')
@login_required
def task_delete(tid):
    conn = get_db()
    conn.execute('DELETE FROM tasks WHERE id=? AND username=? AND submitted=0', (tid, session['user']))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/submit/email')
@login_required
def submit_email():
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    tasks = conn.execute('SELECT * FROM tasks WHERE username=? AND date=? AND submitted=0',
                         (session['user'], today)).fetchall()
    emails = conn.execute('SELECT value FROM settings WHERE key=?', ('default_emails',)).fetchone()
    conn.close()
    if not tasks:
        flash('No tasks to submit')
        return redirect(url_for('dashboard'))
    # Build CSV
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Date','Username','Equipment','Hours','Resolved','WorkOrder','WONumber','Notes'])
    for t in tasks:
        w.writerow([t['date'], t['username'], t['equipment'], t['hours'],
                    'Yes' if t['resolved'] else 'No', 'Yes' if t['has_wo'] else 'No',
                    t['wo_number'] or '', t['notes'] or ''])
    csv_data = buf.getvalue()
    # mailto with body (limited) - better to offer download + mailto
    session['pending_csv'] = csv_data
    session['pending_ids'] = [t['id'] for t in tasks]
    return render_template('email_submit.html', emails=(emails['value'] if emails else ''),
                           count=len(tasks), today=today)

@app.route('/submit/confirm', methods=['POST'])
@login_required
def submit_confirm():
    ids = session.pop('pending_ids', [])
    if ids:
        conn = get_db()
        conn.execute(f'UPDATE tasks SET submitted=1 WHERE id IN ({",".join("?"*len(ids))})', ids)
        conn.commit()
        conn.close()
    flash('Tasks marked submitted')
    return redirect(url_for('dashboard'))

@app.route('/download/csv')
@login_required
def download_csv():
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    tasks = conn.execute('SELECT * FROM tasks WHERE username=? AND date=? AND submitted=0',
                         (session['user'], today)).fetchall()
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Date','Username','Equipment','Hours','Resolved','WorkOrder','WONumber','Notes'])
    for t in tasks:
        w.writerow([t['date'], t['username'], t['equipment'], f"{t['hours']:.2f}",
                    'Yes' if t['resolved'] else 'No', 'Yes' if t['has_wo'] else 'No',
                    t['wo_number'] or '', t['notes'] or ''])
    buf.seek(0)
    return send_file(io.BytesIO(buf.getvalue().encode()), mimetype='text/csv',
                     as_attachment=True, download_name=f"{session['user']}_{today}.csv")

# Admin
@app.route('/admin')
@admin_required
def admin():
    conn = get_db()
    users = conn.execute('SELECT * FROM users ORDER BY username').fetchall()
    emails = conn.execute('SELECT value FROM settings WHERE key=?', ('default_emails',)).fetchone()
    conn.close()
    return render_template('admin.html', users=users, emails=(emails['value'] if emails else ''))

@app.route('/admin/emails', methods=['POST'])
@admin_required
def admin_emails():
    emails = request.form.get('emails','').strip()
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)', ('default_emails', emails))
    conn.commit()
    conn.close()
    flash('Default emails saved')
    return redirect(url_for('admin'))

@app.route('/admin/user/add', methods=['POST'])
@admin_required
def admin_user_add():
    u = request.form.get('username','').strip()
    p = request.form.get('password','')
    n = request.form.get('full_name','').strip() or u
    admin_flag = 1 if request.form.get('is_admin') else 0
    if not u or not p:
        flash('Username and password required')
        return redirect(url_for('admin'))
    conn = get_db()
    try:
        conn.execute('INSERT INTO users (username, password, full_name, is_admin) VALUES (?,?,?,?)',
                     (u, p, n, admin_flag))
        conn.commit()
        flash('User added')
    except:
        flash('Username already exists')
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:uid>/toggle')
@admin_required
def admin_user_toggle(uid):
    conn = get_db()
    conn.execute('UPDATE users SET is_active = 1 - is_active WHERE id=?', (uid,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:uid>/reset', methods=['POST'])
@admin_required
def admin_user_reset(uid):
    p = request.form.get('password','')
    if p:
        conn = get_db()
        conn.execute('UPDATE users SET password=? WHERE id=?', (p, uid))
        conn.commit()
        conn.close()
        flash('Password reset')
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:uid>/role')
@admin_required
def admin_user_role(uid):
    conn = get_db()
    conn.execute('UPDATE users SET is_admin = 1 - is_admin WHERE id=?', (uid,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

init_db()

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 47823))
    debug = os.environ.get('RENDER') is None
    print(f"ASF Maintenance Task Tracker Web - rev 1.01")
    print(f"Listening on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)

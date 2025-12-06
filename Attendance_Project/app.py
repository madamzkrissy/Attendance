import os
import io
import base64
import pickle
import sqlite3
import logging
from datetime import datetime, time
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, send_from_directory
from werkzeug.utils import secure_filename


try:
    import face_recognition
    import numpy as np
except Exception as e:
    # If either import fails, set both to None so code can gracefully disable face features.
    face_recognition = None
    np = None
    logging.warning('Could not import face_recognition or numpy: %s', e)

UPLOAD_FOLDER = 'uploads'
DB_PATH = 'data.db'
ALLOWED_EXT = {'png', 'jpg', 'jpeg'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'replace-this-with-a-secure-random-key'

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS students (
        sr_code TEXT PRIMARY KEY,
        full_name TEXT,
        program TEXT,
        section TEXT,
        encoding BLOB,
        photo_path TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sr_code TEXT,
        full_name TEXT,
        program TEXT,
        section TEXT,
        timestamp TEXT,
        status TEXT,
        photo_path TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        full_name TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        teacher_id INTEGER,
        program TEXT,
        section TEXT,
        weekday INTEGER,
        start_time TEXT
    )
    ''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def load_known_encodings():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT sr_code, full_name, encoding FROM students')
    rows = cur.fetchall()
    known = []
    for r in rows:
        if r['encoding']:
            enc = pickle.loads(r['encoding'])
            known.append((r['sr_code'], r['full_name'], enc))
    conn.close()
    return known

@app.route('/')
@app.route('/home')
def home():
    return render_template('home.html')

@app.route('/select')
def select():
    return render_template('select.html')

# Student flow: select department/program/section
@app.route('/student/select', methods=['GET','POST'])
def student_select():
    if request.method == 'POST':
        session['program'] = request.form.get('program')
        session['section'] = request.form.get('section')
        return redirect(url_for('student_scan'))
    # programs and sections to choose from
    programs = [
    "Bachelor of Automotive Engineering Technology",
    "Bachelor of Civil Engineering Technology",
    "Bachelor of Computer Engineering Technology",
    "Bachelor of Drafting Engineering Technology",
    "Bachelor of Electrical Engineering Technology",
    "Bachelor of Electronics Engineering Technology",
    "Bachelor of Food Engineering Technology",
    "Bachelor of Instrumentation and Control Engineering Technology",
    "Bachelor of Mechanical Engineering Technology",
    "Bachelor of Mechatronics Engineering Technology",
    "Bachelor of Welding and Fabrication Engineering Technology"
]
    sections = {
    "Bachelor of Automotive Engineering Technology": ['1-A', '1-B'],
    "Bachelor of Civil Engineering Technology": ['1-A', '1-B'],
    "Bachelor of Computer Engineering Technology": ['BCpET 1101', 'BCpET 1102', 'BCpET 1103', 'BCpET 1104', 'BCpET 1105', 'BCpET 1106'],
    "Bachelor of Drafting Engineering Technology": ['1-A', '1-B'],
    "Bachelor of Electrical Engineering Technology": ['1-A', '1-B'],
    "Bachelor of Electronics Engineering Technology": ['1-A', '1-B'],
    "Bachelor of Food Engineering Technology": ['1-A', '1-B'],
    "Bachelor of Instrumentation and Control Engineering Technology": ['1-A', '1-B'],
    "Bachelor of Mechanical Engineering Technology": ['1-A', '1-B'],
    "Bachelor of Mechatronics Engineering Technology": ['1-A', '1-B'],
    "Bachelor of Welding and Fabrication Engineering Technology": ['1-A', '1-B']     
}
    return render_template('student_select.html', programs=programs, sections=sections)

@app.route('/student/scan')
def student_scan():
    # The page contains camera UI which posts snapshots to /api/scan
    program = session.get('program','')
    section = session.get('section','')
    # Indicate whether face recognition is available to the template
    face_enabled = (face_recognition is not None) and (np is not None)
    return render_template('student_scan.html', program=program, section=section, face_enabled=face_enabled)

@app.route('/api/scan', methods=['POST'])
def api_scan():
    if face_recognition is None:
        # Allow a manual check-in fallback when the face_recognition library is not available.
        data = request.json or {}
        manual_sr = data.get('manual_sr_code')
        if manual_sr:
            # look up student and mark attendance
            conn = get_db()
            cur = conn.cursor()
            cur.execute('SELECT sr_code, full_name FROM students WHERE sr_code=?', (manual_sr,))
            row = cur.fetchone()
            if not row:
                conn.close()
                return jsonify({'ok':False,'error':'student_not_found'}), 404
            sr_code = row['sr_code']
            name = row['full_name']
            now = datetime.now()
            status = 'Present'
            late_threshold = time(8,15)
            if now.time() > late_threshold:
                status = 'Late'
            # no photo for manual check-in
            cur.execute('INSERT INTO attendance (sr_code, full_name, program, section, timestamp, status, photo_path) VALUES (?,?,?,?,?,?,?)',
                        (sr_code, name, session.get('program'), session.get('section'), now.isoformat(), status, None))
            conn.commit()
            conn.close()
            return jsonify({'ok':True,'results':[{'sr_code':sr_code,'name':name,'status':status}]})
        return jsonify({'ok':False,'error':'face_recognition library not available on server.'}), 500
    data = request.json
    img_b64 = data.get('image')
    if not img_b64:
        return jsonify({'ok':False,'error':'no image provided'}), 400
    header, encoded = img_b64.split(',',1) if ',' in img_b64 else (None,img_b64)
    image_bytes = base64.b64decode(encoded)
    img = face_recognition.load_image_file(io.BytesIO(image_bytes))
    face_locations = face_recognition.face_locations(img)
    if len(face_locations) == 0:
        return jsonify({'ok':False,'error':'no_face_detected'})
    face_encodings = face_recognition.face_encodings(img, face_locations)
    known = load_known_encodings()
    results = []
    for enc in face_encodings:
        best_match = None
        best_dist = 1.0
        for sr, name, known_enc in known:
            dist = np.linalg.norm(known_enc - enc)
            if dist < best_dist:
                best_dist = dist
                best_match = (sr, name, dist)
        # threshold — tune as needed
        if best_match and best_dist < 0.55:
            sr_code, name, _ = best_match
            # determine status (present/late) — simplified: compare to 08:15
            now = datetime.now()
            status = 'Present'
            # Example: late threshold
            late_threshold = time(8,15)
            if now.time() > late_threshold:
                status = 'Late'
            # save photo
            filename = f"{sr_code}_{int(datetime.utcnow().timestamp())}.jpg"
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
            with open(path, 'wb') as f:
                f.write(image_bytes)
            # log attendance
            conn = get_db()
            cur = conn.cursor()
            cur.execute('INSERT INTO attendance (sr_code, full_name, program, section, timestamp, status, photo_path) VALUES (?,?,?,?,?,?,?)',
                        (sr_code, name, session.get('program'), session.get('section'), now.isoformat(), status, path))
            conn.commit()
            conn.close()
            results.append({'sr_code':sr_code,'name':name,'status':status})
        else:
            results.append({'sr_code':None,'name':None,'status':'unknown'})
    return jsonify({'ok':True,'results':results})

@app.route('/student/signup', methods=['GET','POST'])
def student_signup():
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        sr_code = request.form.get('sr_code')
        email = request.form.get('email')
        program = request.form.get('program')
        section = request.form.get('section')
        img_b64 = request.form.get('image')
        if not (sr_code and full_name and img_b64):
            return 'Missing fields', 400
        header, encoded = img_b64.split(',',1) if ',' in img_b64 else (None,img_b64)
        image_bytes = base64.b64decode(encoded)
        # save photo
        filename = f"{sr_code}_profile.jpg"
        path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
        with open(path, 'wb') as f:
            f.write(image_bytes)
        enc_blob = None
        if face_recognition:
            img = face_recognition.load_image_file(io.BytesIO(image_bytes))
            faces = face_recognition.face_encodings(img)
            if not faces:
                return 'No face detected', 400
            enc = faces[0]
            enc_blob = pickle.dumps(enc)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('REPLACE INTO students (sr_code, full_name, program, section, encoding, photo_path) VALUES (?,?,?,?,?,?)',
                (sr_code, full_name, program, section, enc_blob, path))
        conn.commit()
        conn.close()
        return redirect(url_for('student_scan'))
    programs = [
        "Bachelor of Automotive Engineering Technology",
        "Bachelor of Civil Engineering Technology",
        "Bachelor of Computer Engineering Technology",
        "Bachelor of Drafting Engineering Technology",
        "Bachelor of Electrical Engineering Technology",
        "Bachelor of Electronics Engineering Technology",
        "Bachelor of Food Engineering Technology",
        "Bachelor of Instrumentation and Control Engineering Technology",
        "Bachelor of Mechanical Engineering Technology",
        "Bachelor of Mechatronics Engineering Technology",
        "Bachelor of Welding and Fabrication Engineering Technology"
    ]
    sections = {
        "Bachelor of Automotive Engineering Technology": ['1-A', '1-B'],
        "Bachelor of Civil Engineering Technology": ['1-A', '1-B'],
        "Bachelor of Computer Engineering Technology": ['BCpET 1101', 'BCpET 1102', 'BCpET 1103', 'BCpET 1104', 'BCpET 1105', 'BCpET 1106'],
        "Bachelor of Drafting Engineering Technology": ['1-A', '1-B'],
        "Bachelor of Electrical Engineering Technology": ['1-A', '1-B'],
        "Bachelor of Electronics Engineering Technology": ['1-A', '1-B'],
        "Bachelor of Food Engineering Technology": ['1-A', '1-B'],
        "Bachelor of Instrumentation and Control Engineering Technology": ['1-A', '1-B'],
        "Bachelor of Mechanical Engineering Technology": ['1-A', '1-B'],
        "Bachelor of Mechatronics Engineering Technology": ['1-A', '1-B'],
        "Bachelor of Welding and Fabrication Engineering Technology": ['1-A', '1-B']     
}
    return render_template('signup.html', programs=programs, sections=sections)

@app.route('/student/update', methods=['GET','POST'])
def student_update():
    if request.method == 'POST':
        sr_code = request.form.get('sr_code')
        img_b64 = request.form.get('image')
        if not sr_code or not img_b64:
            return 'Missing', 400
        header, encoded = img_b64.split(',',1) if ',' in img_b64 else (None,img_b64)
        image_bytes = base64.b64decode(encoded)
        path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(f"{sr_code}_profile.jpg"))
        with open(path, 'wb') as f:
            f.write(image_bytes)
        enc_blob = None
        if face_recognition:
            img = face_recognition.load_image_file(io.BytesIO(image_bytes))
            faces = face_recognition.face_encodings(img)
            if not faces:
                return 'No face detected', 400
            enc_blob = pickle.dumps(faces[0])
        conn = get_db()
        cur = conn.cursor()
        cur.execute('UPDATE students SET encoding=?, photo_path=? WHERE sr_code=?', (enc_blob, path, sr_code))
        conn.commit()
        conn.close()
        return redirect(url_for('student_scan'))
    return render_template('update.html')

@app.route('/teacher')
def teacher_home():
    return render_template('teacher_home.html')

@app.route('/teacher/dashboard')
def teacher_dashboard():
    # Manage students: list students with edit/delete actions
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT sr_code, full_name, program, section, photo_path FROM students ORDER BY full_name')
    students = cur.fetchall()
    conn.close()
    return render_template('teacher_dashboard.html', students=students)


@app.route('/teacher/student/edit/<sr_code>', methods=['GET','POST'])
def teacher_student_edit(sr_code):
    conn = get_db()
    cur = conn.cursor()
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        program = request.form.get('program')
        section = request.form.get('section')
        cur.execute('UPDATE students SET full_name=?, program=?, section=? WHERE sr_code=?',
                    (full_name, program, section, sr_code))
        conn.commit()
        conn.close()
        return redirect(url_for('teacher_dashboard'))
    cur.execute('SELECT * FROM students WHERE sr_code=?', (sr_code,))
    student = cur.fetchone()
    conn.close()
    if not student:
        return 'Student not found', 404
    return render_template('teacher_student_edit.html', student=student)


@app.route('/teacher/student/delete/<sr_code>', methods=['POST'])
def teacher_student_delete(sr_code):
    conn = get_db()
    cur = conn.cursor()
    # optionally delete related attendance
    cur.execute('DELETE FROM attendance WHERE sr_code=?', (sr_code,))
    cur.execute('DELETE FROM students WHERE sr_code=?', (sr_code,))
    conn.commit()
    conn.close()
    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/reports')
def teacher_reports():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM attendance ORDER BY timestamp DESC LIMIT 1000')
    rows = cur.fetchall()
    conn.close()
    return render_template('teacher_reports.html', logs=rows)

@app.route('/teacher/schedule', methods=['GET','POST'])
def teacher_schedule():
    if request.method == 'POST':
        # store schedule entry
        teacher_email = request.form.get('email')
        program = request.form.get('program')
        section = request.form.get('section')
        weekday = int(request.form.get('weekday',0))
        start_time = request.form.get('start_time')
        conn = get_db()
        cur = conn.cursor()
        # simple teacher lookup/insert
        cur.execute('SELECT id FROM teachers WHERE email=?', (teacher_email,))
        row = cur.fetchone()
        if row:
            teacher_id = row['id']
        else:
            cur.execute('INSERT INTO teachers (email, full_name) VALUES (?,?)', (teacher_email, ''))
            teacher_id = cur.lastrowid
        cur.execute('INSERT INTO schedules (teacher_id, program, section, weekday, start_time) VALUES (?,?,?,?,?)',
                (teacher_id, program, section, weekday, start_time))
        conn.commit()
        conn.close()
        return redirect(url_for('teacher_schedule'))
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM schedules ORDER BY id DESC')
    schedules = cur.fetchall()
    conn.close()
    return render_template('teacher_schedule.html', schedules=schedules)


@app.route('/teacher/schedule/delete/<int:schedule_id>', methods=['POST'])
def teacher_schedule_delete(schedule_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM schedules WHERE id=?', (schedule_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('teacher_schedule'))


@app.route('/teacher/schedule/edit/<int:schedule_id>', methods=['GET','POST'])
def teacher_schedule_edit(schedule_id):
    conn = get_db()
    cur = conn.cursor()
    if request.method == 'POST':
        program = request.form.get('program')
        section = request.form.get('section')
        weekday = int(request.form.get('weekday', 0))
        start_time = request.form.get('start_time')
        cur.execute('UPDATE schedules SET program=?, section=?, weekday=?, start_time=? WHERE id=?',
                    (program, section, weekday, start_time, schedule_id))
        conn.commit()
        conn.close()
        return redirect(url_for('teacher_schedule'))
    cur.execute('SELECT * FROM schedules WHERE id=?', (schedule_id,))
    sched = cur.fetchone()
    conn.close()
    if not sched:
        return 'Schedule not found', 404
    return render_template('teacher_schedule_edit.html', sched=sched)

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

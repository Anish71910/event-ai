from getpass import getpass
from app import app,db
from werkzeug.security import generate_password_hash
with app.app_context():
    email=input('Admin email: ').strip().lower()
    password=getpass('Password (at least 12 characters): ')
    if '@' not in email or len(password)<12:raise SystemExit('Invalid email or password too short.')
    db().execute("INSERT INTO users(name,email,password,role,verified,approved) VALUES(?,?,?,'admin',1,1)",('Administrator',email,generate_password_hash(password)))
    db().commit()
    print('Administrator created.')

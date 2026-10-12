"""Read-only deployment configuration check. Never prints credential values."""
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from production import validate_environment

def main():
    load_dotenv()
    config=dict(os.environ,APP_ENV='production')
    errors=validate_environment(config)
    database=config.get('DATABASE','')
    if not database or not Path(database).is_file():
        errors.append('An initialized persistent database is required; create the administrator before this check.')
    else:
        try:
            connection=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)
            with connection:
                if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':errors.append('Database integrity check failed.')
                if connection.execute("SELECT 1 FROM users WHERE email LIKE '%@smartevents.demo' LIMIT 1").fetchone():errors.append('Demo accounts are present. Use a clean database for public operation.')
                if not connection.execute("SELECT 1 FROM users WHERE role='admin' AND verified=1 LIMIT 1").fetchone():errors.append('Create a verified administrator.')
            connection.close()
        except sqlite3.Error:errors.append('Database cannot be read or has not been initialized.')
    for error in errors:print('BLOCKED: '+error)
    if not errors:print('Configuration checks passed. This is not a production certification.')
    print('Manual release gates: HTTPS and SMTP delivery; payment webhooks/refunds for paid events; browser/mobile QA; load/security testing; restore drill and privacy policy review.')
    return 1 if errors else 0

if __name__=='__main__':raise SystemExit(main())

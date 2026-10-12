"""Consistent SQLite backup, including WAL transactions; never overwrites a file."""
import argparse
import os
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
from dotenv import load_dotenv

def main():
    load_dotenv()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    source=Path(os.getenv('DATABASE',str(Path(__file__).parent/'instance'/'events.db'))).resolve()
    if not source.is_file():raise SystemExit('Database not found. Check DATABASE.')
    folder=Path(args.output_dir).resolve();folder.mkdir(parents=True,exist_ok=True)
    target=folder/('eventai-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.db')
    with target.open('xb'):pass
    target.chmod(0o600)
    read=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    write=sqlite3.connect(str(target))
    try:
        read.backup(write)
        if write.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity check failed.')
    finally:read.close();write.close()
    print('Backup created: '+str(target))
    print('Contains private account and booking data. Encrypt and restrict access; test restoration on a separate database.')

if __name__=='__main__':main()

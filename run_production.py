"""Start EventAI behind an HTTPS reverse proxy with mandatory production checks."""
import os
from dotenv import load_dotenv

load_dotenv()
os.environ['APP_ENV']='production'

from app import app
from waitress import serve

if __name__=='__main__':
    serve(app,host='127.0.0.1',port=int(os.getenv('PORT','5000')),threads=4)

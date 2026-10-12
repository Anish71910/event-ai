"""Run under a process supervisor; --once supports a one-minute scheduled task."""
import os,time,sys
os.environ['EMAIL_WORKER']='0'
from app import app

def main():
    while True:
        with app.app_context():result=app.extensions['eventai_notifications_tick']()
        print('Notification delivery:',result,flush=True)
        if '--once' in sys.argv:return
        time.sleep(30)
if __name__=='__main__':main()

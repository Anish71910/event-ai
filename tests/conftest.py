import os
import tempfile
# Never send email or start a background worker from automated tests.
os.environ['SMTP_PASSWORD']=''
os.environ['EVENTAI_WORKER']='0'
os.environ['APP_ENV']='development'
os.environ['DEMO_MODE']='1'
os.environ['PAYMENT_PROVIDER']='demo'
os.environ['RAZORPAY_KEY_ID']=''
os.environ['RAZORPAY_KEY_SECRET']=''
os.environ['RAZORPAY_WEBHOOK_SECRET']=''
_test_storage=tempfile.TemporaryDirectory(prefix='eventai-tests-')
os.environ['DATABASE']=os.path.join(_test_storage.name,'events.db')

import pytest

@pytest.fixture(autouse=True)
def reset_test_throttles():
    # Each scenario receives its own rate-limit window; production limits remain unchanged.
    from app import app,db
    with app.app_context():
        db().execute('DELETE FROM throttle');db().commit()

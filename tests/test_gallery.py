import base64
import io
from PIL import Image
import app as module
from test_recommendations import rec


def test_gallery_upload_ownership_review_and_limits(rec):
    c,p,login=rec
    image=io.BytesIO();Image.new('RGB',(8,8),'purple').save(image,format='PNG')
    photo=base64.b64encode(image.getvalue()).decode()
    assert p('gallery/100',{'photo':photo}).status_code==403
    login(2)
    assert p('gallery/100',{'photo':'not an image'}).status_code==400
    for _ in range(6):assert p('gallery/100',{'photo':photo}).status_code==200
    assert p('gallery/100',{'photo':photo}).status_code==400
    photos=c.get('/api/gallery/100').json;pid=photos[0]['id']
    assert c.get('/api/gallery-image/'+str(pid)).mimetype=='image/jpeg'
    login(10)
    assert c.get('/api/gallery/100').status_code==400
    assert c.get('/api/gallery-image/'+str(pid)).status_code==400
    login(3);assert p('admin/review',{'type':'event','id':100,'status':'approved'}).status_code==200
    login(10);assert len(c.get('/api/gallery/100').json)==6
    login(2);assert p('gallery/100',{'remove':pid}).status_code==200
    assert len(c.get('/api/gallery/100').json)==5
    with module.app.app_context():
        module.db().execute("UPDATE events SET date='2020-01-01' WHERE id=100");module.db().commit()
    assert p('gallery/100',{'photo':photo}).status_code==400

"""Bounded public site decoration and raster assets."""
import base64
import hashlib
import io
import re
from urllib.parse import urlsplit
from PIL import Image, UnidentifiedImageError
from fastapi.responses import FileResponse

DEFAULT = dict(site_name='影视服',site_subtitle='MediaDock',site_description='观影服务平台',logo_url='',favicon_url='',hero_eyebrow='A LITTLE LIGHT. A WORLD OF STORIES.',hero_title='把好电影，',hero_highlight='留给每个夜晚。',hero_description='让喧嚣留在门外，让故事照亮生活。\n从一场电影开始，拥有属于你的光影时刻。',footer_text='让好影像融入生活',hero_images=[],theme_mode='auto',day_start='07:00',night_start='19:00')
LIMITS=dict(site_name=32,site_subtitle=40,site_description=240,hero_eyebrow=100,hero_title=80,hero_highlight=80,hero_description=1000,footer_text=160)

def image_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(c.isspace() or ord(c)<32 or c=='\\' for c in value): raise ValueError('图片地址格式不正确')
    if not value:return ''
    if value.startswith('/static/') or re.fullmatch(r'/api/public/appearance-assets/[a-f0-9]{64}\.(png|jpg|webp)',value):
        if '..' not in value and '%' not in value and '?' not in value and '#' not in value:return value
    try:
        u=urlsplit(value)
        if u.scheme=='https' and u.hostname and not u.username and not u.password and not u.fragment:return value
    except ValueError:pass
    raise ValueError('图片请上传到本站，或填写 HTTPS 图片地址')

def config(value=None, previous=None):
    if value is None:value={}
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise ValueError('页面装修包含不支持的字段')
    result={**DEFAULT,**(previous or {}),**value}
    for key,limit in LIMITS.items():
        text=result[key]
        if not isinstance(text,str) or len(text)>limit or any(ord(c)<32 and c not in '\n\t' or ord(c)==127 for c in text):raise ValueError('页面文字过长或格式不正确')
        result[key]=text.strip()
    if not result['site_name']:raise ValueError('请填写站点名称')
    for key in ['logo_url','favicon_url']:result[key]=image_url(result[key])
    for key in ['day_start','night_start']:
        if not isinstance(result[key],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',result[key]):raise ValueError('请填写有效的切换时间')
    if result['day_start']==result['night_start']:raise ValueError('白天与夜间开始时间不能相同')
    if result['theme_mode'] not in ('dark','light','auto'):raise ValueError('请选择有效主题')
    slides=result['hero_images']
    if not isinstance(slides,list) or len(slides)>6:raise ValueError('最多设置 6 张首页图片')
    clean=[]
    for item in slides:
        if not isinstance(item,dict) or set(item)!={'url','title','subtitle'}:raise ValueError('首页图片格式不正确')
        url=image_url(item['url'])
        if not url:raise ValueError('请设置首页图片地址')
        for key in ('title','subtitle'):
            if not isinstance(item[key],str) or len(item[key])>200 or any(ord(c)<32 for c in item[key]):raise ValueError('图片说明过长或格式不正确')
        clean.append(dict(url=url,title=item['title'],subtitle=item['subtitle']))
    result['hero_images']=clean
    return result

def upload(body,directory):
    if not isinstance(body,dict) or set(body)-{'filename','data'} or not isinstance(body.get('data'),str) or len(body['data'])>2800000:raise ValueError('请上传 2 MB 以内的 PNG、JPEG 或 WebP 图片')
    try:raw=base64.b64decode(body['data'],validate=True)
    except (ValueError,TypeError):raise ValueError('图片内容无法读取')
    if not raw or len(raw)>2*1024*1024:raise ValueError('图片最大为 2 MB')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('PNG','JPEG','WEBP') or im.width*im.height>16000000:raise ValueError('仅支持 1600 万像素以内的 PNG、JPEG、WebP')
            im.seek(0);im.load();fmt=im.format;clean=im.convert('RGB' if fmt=='JPEG' else 'RGBA');buf=io.BytesIO();clean.save(buf,format=fmt);data=buf.getvalue()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):raise ValueError('图片损坏或尺寸过大')
    if len(data)>8*1024*1024:raise ValueError('图片处理后过大，请压缩后上传')
    suffix={'PNG':'png','JPEG':'jpg','WEBP':'webp'}[fmt];name=hashlib.sha256(data).hexdigest()+'.'+suffix
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/name
    if not target.exists():
        if sum(x.stat().st_size for x in directory.iterdir() if x.is_file())+len(data)>128*1024*1024:raise ValueError('装修图片存储已满，请联系管理员整理历史图片')
        target.write_bytes(data)
    return {'url':'/api/public/appearance-assets/'+name}

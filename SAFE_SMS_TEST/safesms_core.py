from __future__ import annotations
import base64, hashlib, json, os, struct, zlib
from pathlib import Path
from typing import Iterable

MAGIC = "SMS3"
VERSION = 3

ROAD_PENALTY = {
    'motorway':4.0,'motorway_link':3.2,'trunk':3.2,'trunk_link':2.8,
    'primary':2.4,'primary_link':2.0,'secondary':1.8,'secondary_link':1.6,
    'tertiary':1.35,'tertiary_link':1.25,'unclassified':1.0,
    'residential':0.85,'living_street':0.75,'service':1.10,
    'road':1.0,'track':1.15,'cycleway':0.65,'path':0.75,
    'footway':0.80,'pedestrian':0.78,'steps':2.0,'bridleway':0.9,
}

REASON_CODES = {
    'flood':'FL','traffic accident':'AC','fire':'FI','debris':'DE',
    'building collapse':'CO','hazmat / chemical':'HZ','police incident':'PO',
    'crowd / event':'CR','fallen tree':'TR','landslide':'LS','other':'OT'
}
REASON_NAMES = {v:k for k,v in REASON_CODES.items()}


def package_id(city: str, hex_size: int, graph_version: str='1', network: str='drive') -> str:
    raw=f'{city.strip().lower()}|{hex_size}|{graph_version}|{network}'.encode()
    return hashlib.sha256(raw).hexdigest()[:10].upper()


def grid_fingerprint(grid) -> str:
    h=hashlib.sha256()
    for _,r in grid.sort_values('grid_id').iterrows():
        h.update(str(int(r.grid_id)).encode()+b'|')
        h.update(r.geometry.wkb)
    return h.hexdigest()[:10].upper()


def route_weight(data, mode='vehicle'):
    highway=data.get('highway','unclassified')
    if isinstance(highway,(list,tuple)): highway=highway[0]
    highway=str(highway)
    p=ROAD_PENALTY.get(highway,1.0)
    if mode=='bicycle':
        if highway in {'cycleway','path','track','residential','living_street'}: p*=0.65
        if highway in {'motorway','trunk','primary'}: p*=1.7
    elif mode=='walking':
        if highway in {'footway','pedestrian','path','living_street'}: p*=0.55
        if highway in {'motorway','trunk','primary','secondary'}: p*=2.5
    length=float(data.get('length',1.0) or 1.0)
    return length*p


def _varint(n: int) -> bytes:
    if n < 0: raise ValueError('varint cannot encode negative values')
    out=bytearray()
    while n >= 128:
        out.append((n & 127) | 128); n >>= 7
    out.append(n)
    return bytes(out)


def _read_varint(data: bytes, i: int):
    n=0; shift=0
    while True:
        if i >= len(data): raise ValueError('truncated SafeSMS varint')
        b=data[i]; i += 1
        n |= (b & 127) << shift
        if b < 128: return n, i
        shift += 7
        if shift > 63: raise ValueError('invalid SafeSMS varint')


def _encode_payload(payload: dict) -> str:
    """Compact binary SMS payload. Reasons are two-character codes and tile IDs are varints."""
    body=bytearray(MAGIC.encode()+bytes([VERSION]))
    body += bytes.fromhex(payload['p'])
    body += bytes.fromhex(payload['g'])
    flags={'vehicle':0,'bicycle':4,'walking':8}[payload['mode']]
    if payload['preference']=='fastest': flags |= 1
    if payload.get('o') and payload.get('d'): flags |= 2
    body.append(flags)
    if flags & 2:
        coords=tuple(round(float(x)*1e5) for x in (*payload['o'],*payload['d']))
    else:
        coords=(0,0,0,0)
    body += struct.pack('>iiii',*coords)
    body += struct.pack('>II',int(payload.get('iat',0) or 0),int(payload.get('exp',0) or 0))
    tiles=payload.get('t',[])
    body += _varint(len(tiles))
    prev=0
    for t in sorted(tiles,key=lambda x:int(x['i'])):
        tid=int(t['i']); body += _varint(tid-prev); prev=tid
        body.append(max(1,min(5,int(t.get('s',3)))))
        rc=str(t.get('r','OT'))[:2].upper().ljust(2,'O')
        body += rc.encode('ascii')
    crc=zlib.crc32(body)&0xffffffff
    body += struct.pack('>I',crc)
    return 'SMS3-' + base64.b32encode(bytes(body)).decode().rstrip('=')


def _decode_payload(code: str) -> dict:
    code=code.strip().replace(' ','').replace('-','')
    if code.startswith(MAGIC): encoded=code[len(MAGIC):]
    else: raise ValueError('Unsupported SafeSMS code')
    pad='='*((8-len(encoded)%8)%8)
    raw=base64.b32decode(encoded+pad)
    prefix=MAGIC.encode()+bytes([VERSION])
    if raw[:5]!=prefix: raise ValueError('Unsupported SafeSMS version')
    expected=struct.unpack('>I',raw[-4:])[0]
    actual=zlib.crc32(raw[:-4])&0xffffffff
    if expected!=actual: raise ValueError('Invalid SafeSMS checksum')
    i=5
    pid=raw[i:i+5].hex().upper(); i+=5
    gh=raw[i:i+5].hex().upper(); i+=5
    flags=raw[i]; i+=1
    vals=struct.unpack('>iiii',raw[i:i+16]); i+=16
    iat,exp=struct.unpack('>II',raw[i:i+8]); i+=8
    n,i=_read_varint(raw,i)
    tiles=[]; prev=0
    for _ in range(n):
        delta,i=_read_varint(raw,i); tid=prev+delta; prev=tid
        severity=raw[i]; i+=1
        reason=raw[i:i+2].decode('ascii'); i+=2
        tiles.append({'i':tid,'s':severity,'r':reason})
    origin=destination=None
    if flags & 2:
        origin=(vals[0]/1e5,vals[1]/1e5); destination=(vals[2]/1e5,vals[3]/1e5)
    return {'v':VERSION,'p':pid,'g':gh,'m':{0:'vehicle',4:'bicycle',8:'walking'}.get(flags&12,'vehicle'),
            'q':'fastest' if flags&1 else 'easiest','o':origin,'d':destination,'t':tiles,
            'iat':iat,'exp':exp}


def _to_epoch(value):
    if not value: return 0
    if isinstance(value,(int,float)): return int(value)
    from datetime import datetime
    return int(datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp())


def encode_sms(city:str, hex_size:int, grid_hash:str, blocked_tiles:Iterable[int],
               origin=None, destination=None, mode='vehicle', preference='easiest',
               network='drive', incidents=None, issued_at=None, expires_at=None) -> str:
    incidents=incidents or {}
    tiles=[]
    for tid in sorted(set(int(x) for x in blocked_tiles)):
        item=incidents.get(str(tid), incidents.get(tid, {})) or {}
        reason=item.get('reason','other')
        severity=int(item.get('severity',3))
        tiles.append({'i':tid,'s':max(1,min(5,severity)),'r':REASON_CODES.get(reason,'OT')})
    payload={'p':package_id(city,hex_size,network=network),'g':grid_hash,'mode':mode,
             'preference':preference,'o':origin,'d':destination,'t':tiles,
             'iat':_to_epoch(issued_at),'exp':_to_epoch(expires_at)}
    return _encode_payload(payload)


def decode_sms(code:str)->dict:
    p=_decode_payload(code)
    tiles=[]
    for x in p.get('t',[]):
        rc=x.get('r','OT')
        tiles.append({'grid_id':int(x['i']),'severity':int(x.get('s',3)),'reason':REASON_NAMES.get(rc,rc)})
    from datetime import datetime, timezone
    fmt=lambda x: datetime.fromtimestamp(x,timezone.utc).isoformat() if x else None
    return {'package_id':p.get('p'),'grid_hash':p.get('g'),'blocked_tiles':[x['grid_id'] for x in tiles],
            'incidents':{str(x['grid_id']):{'severity':x['severity'],'reason':x['reason']} for x in tiles},
            'tiles':tiles,'origin':tuple(p['o']) if p.get('o') else None,
            'destination':tuple(p['d']) if p.get('d') else None,'preference':p.get('q','easiest'),
            'mode':p.get('m','vehicle'),'version':p.get('v',VERSION),
            'issued_at':fmt(p.get('iat',0)),'expires_at':fmt(p.get('exp',0))}

def send_sms_twilio(to_number:str, message:str, from_number: str|None=None) -> str:
    """Send an SMS through Twilio. Credentials are read from environment variables or Streamlit secrets.

    Required: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER.
    For Streamlit, the same keys can be stored in .streamlit/secrets.toml.
    """
    try:
        from twilio.rest import Client
    except ImportError as e:
        raise RuntimeError('Twilio is not installed. Run: pip install twilio') from e
    sid=os.getenv('TWILIO_ACCOUNT_SID')
    token=os.getenv('TWILIO_AUTH_TOKEN')
    sender=from_number or os.getenv('TWILIO_FROM_NUMBER')
    if not sid or not token or not sender:
        try:
            import streamlit as st
            sid=sid or st.secrets.get('TWILIO_ACCOUNT_SID')
            token=token or st.secrets.get('TWILIO_AUTH_TOKEN')
            sender=sender or st.secrets.get('TWILIO_FROM_NUMBER')
        except Exception:
            pass
    if not sid or not token or not sender:
        raise RuntimeError('Missing Twilio credentials. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER.')
    client=Client(sid,token)
    msg=client.messages.create(body=message,from_=sender,to=to_number)
    return msg.sid

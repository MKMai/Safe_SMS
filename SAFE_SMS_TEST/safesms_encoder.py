#!/usr/bin/env python
from __future__ import annotations
import argparse, json
from pathlib import Path
import geopandas as gpd
from safesms_core import encode_sms, grid_fingerprint

def main():
    p=argparse.ArgumentParser(description='SafeSMS emergency-code encoder and SMS sender')
    p.add_argument('--city',required=True)
    p.add_argument('--package',required=True)
    p.add_argument('--blocked',required=True,help='Comma-separated grid IDs')
    p.add_argument('--origin',nargs=2,type=float,metavar=('LAT','LON'))
    p.add_argument('--destination',nargs=2,type=float,metavar=('LAT','LON'))
    p.add_argument('--mode',choices=['vehicle','bicycle','walking'],default='vehicle')
    p.add_argument('--network',choices=['drive','bike','walk'],default='drive')
    p.add_argument('--preference',choices=['fastest','easiest'],default='easiest')
    p.add_argument('--incidents',help='JSON file: {"12":{"reason":"flood","severity":5}}')
    p.add_argument('--expires-minutes',type=int,default=180)
    p.add_argument('--send-to',help='Phone number in E.164 format, e.g. +38640123456')
    a=p.parse_args()
    pkg=Path(a.package)
    manifest=json.loads((pkg/'manifest.json').read_text())
    grid=gpd.read_file(pkg/manifest['grid_file'])
    gh=grid_fingerprint(grid)
    ids=[int(x) for x in a.blocked.split(',') if x.strip()]
    valid=set(grid.grid_id.astype(int)); bad=sorted(set(ids)-valid)
    if bad: raise SystemExit(f'Unknown grid IDs: {bad}')
    incidents=json.loads(Path(a.incidents).read_text()) if a.incidents else {}
    from datetime import datetime, timedelta, timezone
    now=datetime.now(timezone.utc)
    exp=now+timedelta(minutes=a.expires_minutes) if a.expires_minutes else None
    code=encode_sms(a.city,int(manifest['hex_size']),gh,ids,a.origin,a.destination,a.mode,a.preference,a.network,incidents,now.isoformat(),exp.isoformat() if exp else None)
    print('\nSAFE SMS CODE\n'); print(code); print(f'Characters: {len(code)}'); print(f'Blocked tiles: {len(ids)}'); print(f'Grid hash: {gh}')
    if a.send_to:
        from safesms_core import send_sms_twilio
        sid=send_sms_twilio(a.send_to,code)
        print(f'SMS SENT • SID: {sid}')
    else:
        print('SMS not sent. Use --send-to +XXXXXXXXXXX to send via Twilio.')

if __name__=='__main__': main()

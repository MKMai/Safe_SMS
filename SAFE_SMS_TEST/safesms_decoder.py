#!/usr/bin/env python
from safesms_core import decode_sms
import argparse, json

def main():
    p=argparse.ArgumentParser(description='Decode a SafeSMS emergency code')
    p.add_argument('code')
    p.add_argument('--json',action='store_true')
    a=p.parse_args()
    try: d=decode_sms(a.code)
    except Exception as e: raise SystemExit(f'INVALID CODE: {e}')
    if a.json: print(json.dumps(d,indent=2))
    else:
        print('VALID SAFESMS CODE')
        for k,v in d.items(): print(f'{k}: {v}')
if __name__=='__main__': main()

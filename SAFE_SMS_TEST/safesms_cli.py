#!/usr/bin/env python
"""Single terminal entry point for SafeSMS preparation, encoding, decoding and SMS sending."""
import argparse, subprocess, sys

def main():
    p=argparse.ArgumentParser(prog='safesms',description='SafeSMS terminal control')
    sub=p.add_subparsers(dest='command',required=True)
    for name,script in [('prepare','safesms_prepare_city.py'),('tiles','safesms_tiles.py'),('encode','safesms_encoder.py'),('decode','safesms_decoder.py')]:
        sp=sub.add_parser(name); sp.add_argument('args',nargs=argparse.REMAINDER); sp.set_defaults(script=script)
    a=sub.parse_args()
    cmd=[sys.executable,a.script,*a.args]
    raise SystemExit(subprocess.call(cmd))
if __name__=='__main__': main()

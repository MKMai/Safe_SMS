#!/usr/bin/env python
from __future__ import annotations
import argparse, json, math, time
from pathlib import Path
import requests
import geopandas as gpd
import osmnx as ox
from safesms_core import package_id

def lonlat_to_tile(lon,lat,z):
    lat=max(-85.05112878,min(85.05112878,lat)); n=2**z
    x=int((lon+180)/360*n)
    y=int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
    return x,y

def download_tiles(bbox,zmin,zmax,out,url_template,max_tiles=5000):
    west,south,east,north=bbox
    jobs=[]
    for z in range(zmin,zmax+1):
        x0,y1=lonlat_to_tile(west,south,z); x1,y0=lonlat_to_tile(east,north,z)
        for x in range(min(x0,x1),max(x0,x1)+1):
            for y in range(min(y0,y1),max(y0,y1)+1): jobs.append((z,x,y))
    if len(jobs)>max_tiles: raise SystemExit(f'{len(jobs)} tiles requested; increase --max-tiles or reduce zoom range')
    s=requests.Session(); s.headers['User-Agent']='SafeSMS-EmergencyNavigator/1.0'
    for i,(z,x,y) in enumerate(jobs,1):
        p=out/str(z)/str(x)/f'{y}.png'; p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists(): continue
        r=s.get(url_template.format(z=z,x=x,y=y),timeout=20); r.raise_for_status(); p.write_bytes(r.content)
        if i%25==0: print(f'{i}/{len(jobs)} tiles')
        time.sleep(.08)
    return len(jobs)

def main():
    p=argparse.ArgumentParser(description='Download a permitted XYZ basemap tile cache for a SafeSMS city package')
    p.add_argument('--package',required=True)
    p.add_argument('--zmin',type=int,default=12); p.add_argument('--zmax',type=int,default=15)
    p.add_argument('--max-tiles',type=int,default=5000)
    p.add_argument('--tile-url',default='https://tile.openstreetmap.org/{z}/{x}/{y}.png')
    p.add_argument('--bbox',nargs=4,type=float,metavar=('WEST','SOUTH','EAST','NORTH'),help='Optional custom bbox')
    a=p.parse_args(); pkg=Path(a.package); manifest=json.loads((pkg/'manifest.json').read_text())
    if a.bbox: bbox=tuple(a.bbox)
    else:
        aoi=ox.geocoder.geocode_to_gdf(manifest['city']).to_crs(4326); west,south,east,north=aoi.total_bounds; bbox=(west,south,east,north)
    n=download_tiles(bbox,a.zmin,a.zmax,pkg/'tiles',a.tile_url,a.max_tiles)
    manifest['tiles']={'url_template':a.tile_url,'zmin':a.zmin,'zmax':a.zmax,'bbox':bbox,'count':n}
    (pkg/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(f'Offline tile cache ready: {pkg/"tiles"}')
if __name__=='__main__': main()

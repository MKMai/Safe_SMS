#!/usr/bin/env python
from __future__ import annotations
import argparse, json, os, shutil
from pathlib import Path
import geopandas as gpd
import osmnx as ox
from shapely.geometry import Polygon
from safesms_core import package_id, grid_fingerprint
import math

def make_hex_grid(aoi, hex_size=400):
    # Project to a local metric CRS chosen by OSMnx.
    p=ox.projection.project_gdf(aoi)
    xmin,ymin,xmax,ymax=p.total_bounds
    s=float(hex_size); dx=1.5*s; dy=math.sqrt(3)*s
    xmin-=2*s; ymin-=dy; xmax+=2*s; ymax+=dy
    geoms=[]; col=0; x=xmin
    while x<xmax:
        y=ymin+(dy/2 if col%2 else 0)
        while y<ymax:
            geoms.append(Polygon([(x-s,y),(x-s/2,y+dy/2),(x+s/2,y+dy/2),(x+s,y),(x+s/2,y-dy/2),(x-s/2,y-dy/2)])); y+=dy
        x+=dx; col+=1
    grid=gpd.GeoDataFrame({'geometry':geoms},crs=p.crs)
    grid=gpd.overlay(grid,p,how='intersection',keep_geom_type=True)
    grid=grid[['geometry']].explode(index_parts=False).reset_index(drop=True)
    grid['cx']=grid.geometry.centroid.x; grid['cy']=grid.geometry.centroid.y
    grid=grid.sort_values(['cy','cx'],ascending=[False,True]).reset_index(drop=True)
    grid['grid_id']=range(len(grid)); grid['area_ha']=grid.area/10000
    return grid.to_crs(4326)[['grid_id','area_ha','geometry']]

def main():
    p=argparse.ArgumentParser(description='Prepare a city for SafeSMS offline operation')
    p.add_argument('--city',required=True)
    p.add_argument('--output',default='city_packages')
    p.add_argument('--hex-size',type=int,default=400)
    p.add_argument('--network',choices=['drive','bike','walk'],default='drive')
    a=p.parse_args()
    out=Path(a.output)/package_id(a.city,a.hex_size,network=a.network); out.mkdir(parents=True,exist_ok=True)
    print('Downloading boundary...')
    aoi=ox.geocoder.geocode_to_gdf(a.city)
    print('Downloading OSM graph...')
    graph=ox.graph.graph_from_place(a.city,network_type=a.network,simplify=True)
    print('Saving graph...')
    ox.io.save_graphml(graph,out/'graph.graphml')
    print('Creating emergency grid...')
    grid=make_hex_grid(aoi,a.hex_size); grid.to_file(out/'grid.geojson',driver='GeoJSON')
    manifest={'city':a.city,'package_id':package_id(a.city,a.hex_size,network=a.network),'hex_size':a.hex_size,'network':a.network,'grid_hash':grid_fingerprint(grid),'graph_file':'graph.graphml','grid_file':'grid.geojson','version':1}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2)); print(f'Package ready: {out}')
if __name__=='__main__': main()

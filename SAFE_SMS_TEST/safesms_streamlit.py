from __future__ import annotations
import json, math, os, threading, time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import folium
import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd
import streamlit as st
from shapely.geometry import Point
from streamlit_folium import st_folium
from folium.plugins import HeatMap

from safesms_core import (
    decode_sms, encode_sms, grid_fingerprint, route_weight, send_sms_twilio,
    REASON_NAMES, REASON_CODES,
)

st.set_page_config(page_title='EMERGENCY NAVIGATOR | SafeSMS', page_icon='🛡️', layout='wide')

# -----------------------------------------------------------------------------
# UI THEME
# -----------------------------------------------------------------------------
st.markdown('''
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Rajdhani:wght@500;600;700&display=swap');
:root{--bg:#02080d;--panel:#06131c;--panel2:#071a23;--border:#16303d;--text:#dce7eb;--muted:#7f929b;--cyan:#28c8ff;--green:#5bea79;--red:#ff4c43;--yellow:#ffc83d;--purple:#c88cff}
html,body,[class*="css"]{font-family:Inter,sans-serif}.stApp{background:linear-gradient(180deg,#02080d,#030b11);color:var(--text)}
.block-container{max-width:1900px;padding:.45rem .7rem 1rem}.top{height:62px;display:flex;align-items:center;border-bottom:1px solid #10232d;margin-bottom:9px;gap:15px}
.brand{display:flex;align-items:center;gap:10px;min-width:300px}.shield{width:38px;height:38px;border:2px solid #ff3f35;border-radius:9px;color:#ff4c43;display:grid;place-items:center;font-size:19px}.brandmain{font-family:Rajdhani;font-weight:700;letter-spacing:1px;font-size:20px}.brandsub{color:#ff4c43;font-size:11px;letter-spacing:1px}
.chip{margin-left:auto;border:1px solid #17412b;background:#06140f;color:#66e87c;padding:8px 13px;border-radius:6px;font-family:Rajdhani}.headcell{padding:0 16px;border-left:1px solid #13252e}.label{font-size:9px;color:#71838c;letter-spacing:1px}.value{font-size:13px;font-weight:600;margin-top:3px}
.card{background:linear-gradient(180deg,#071720,#041018);border:1px solid var(--border);border-radius:7px;padding:12px}.title{font-family:Rajdhani;font-weight:700;letter-spacing:.8px}.red{color:#ff4c43}.muted{color:#7f929b;font-size:10px}.route{border:1px solid #17313e;border-left:3px solid var(--cyan);border-radius:6px;padding:10px;margin-bottom:8px;background:#061923}.route.green{border-left-color:var(--green)}.route.yellow{border-left-color:var(--yellow)}.rtitle{font-family:Rajdhani;font-weight:700;font-size:15px}.big{font-family:Rajdhani;font-size:23px;font-weight:700}
.metricrow{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid #122731;border-bottom:1px solid #122731}.metric{padding:10px;border-right:1px solid #122731}.mval{font-family:Rajdhani;font-size:21px;color:#28c8ff;font-weight:700}.alert{border:1px solid #7b2e2b;background:#281210;padding:10px;border-radius:6px;color:#ff8179}
.stButton>button{background:#071923;border:1px solid #183746;color:#d8e5e9;border-radius:5px;font-weight:600}.stButton>button:hover{border-color:#26c8ff;color:#26c8ff}.stTextInput input,div[data-baseweb="select"]>div,textarea{background:#06131c!important;border-color:#1a3745!important;color:#dce7eb!important}
[data-testid="stMetricValue"]{font-family:Rajdhani}.smallhelp{font-size:11px;color:#82949c;line-height:1.45}.dangerbox{border:1px solid #7b2e2b;background:#21100f;border-radius:6px;padding:9px;color:#ff8b83}.goodbox{border:1px solid #245d35;background:#07170e;border-radius:6px;padding:9px;color:#74ed8a}
</style>''', unsafe_allow_html=True)

ROOT = Path(st.session_state.get('package_root','city_packages'))
ROOT.mkdir(parents=True, exist_ok=True)
REASONS = list(REASON_CODES.keys())

# -----------------------------------------------------------------------------
# PACKAGE / ROUTING HELPERS
# -----------------------------------------------------------------------------
def packages():
    return [p for p in ROOT.iterdir() if p.is_dir() and (p/'manifest.json').exists()]

@st.cache_resource(show_spinner=False)
def load_package(path_str: str):
    p=Path(path_str)
    m=json.loads((p/'manifest.json').read_text(encoding='utf-8'))
    g=ox.load_graphml(p/m.get('graph_file','graph.graphml'))
    grid=gpd.read_file(p/m.get('grid_file','grid.geojson'))
    if 'grid_id' not in grid.columns: grid['grid_id']=range(len(grid))
    grid['grid_id']=grid['grid_id'].astype(int)
    return m,g,grid

@st.cache_resource(show_spinner=False)
def start_tile_server(root_str: str, port: int = 8765):
    root=Path(root_str)
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(root),**kwargs)
        def log_message(self,format,*args): pass
    try:
        server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        return port
    except OSError:
        return port

def tile_url(pkg: Path):
    if not (pkg/'tiles').exists(): return None
    if not any((pkg/'tiles').rglob('*.png')): return None
    port=start_tile_server(str(ROOT.resolve()))
    return f'http://127.0.0.1:{port}/{pkg.relative_to(ROOT).as_posix()}/tiles/{{z}}/{{x}}/{{y}}.png'

def blocked_graph(graph, grid, blocked):
    blocked=set(int(x) for x in blocked)
    if not blocked: return graph.copy()
    hz=grid[grid.grid_id.isin(blocked)].copy(); g=graph.copy()
    nodes,edges=ox.graph_to_gdfs(g, nodes=True, edges=True)
    try:
        hz_n=gpd.sjoin(nodes.reset_index(),hz[['grid_id','geometry']],predicate='within',how='inner')
        if len(hz_n):
            g.remove_nodes_from(list(hz_n['osmid']))
    except Exception:
        for idx,row in nodes.iterrows():
            if hz.geometry.contains(row.geometry).any(): g.remove_node(idx)
    try:
        edges2=edges.reset_index()
        hz_e=gpd.sjoin(edges2,hz[['grid_id','geometry']],predicate='intersects',how='inner')
        g.remove_edges_from([(r.u,r.v,r.key) for _,r in hz_e.iterrows()])
    except Exception:
        pass
    g.remove_nodes_from(list(nx.isolates(g)))
    return g

def route(graph,o,d,preference,mode):
    if preference=='fastest': return ox.shortest_path(graph,o,d,weight='length')
    g=graph.copy()
    for u,v,k,data in g.edges(keys=True,data=True): data['safesms_weight']=route_weight(data,mode)
    return ox.shortest_path(g,o,d,weight='safesms_weight')

def route_gdf(g,r):
    if not r: return gpd.GeoDataFrame(geometry=[],crs='EPSG:4326')
    return ox.routing.route_to_gdf(g,r,weight='length')

def km(e): return float(e.length.sum())/1000 if len(e) else float('nan')

def severity_color(s):
    return {1:'#ffd54a',2:'#ff9f43',3:'#ff7043',4:'#ff453d',5:'#b40000'}.get(int(s),'#ff453d')

def incidents_for_grid(grid, incidents):
    rows=[]
    for _,r in grid.iterrows():
        item=incidents.get(str(int(r.grid_id)))
        if item:
            rows.append((int(r.grid_id),item.get('severity',3),item.get('reason','other')))
    return rows

def incident_style(grid_id, incidents):
    x=incidents.get(str(int(grid_id)))
    if not x: return {'color':'#24424d','weight':.45,'fillColor':'#071820','fillOpacity':.03}
    c=severity_color(x.get('severity',3))
    return {'color':c,'weight':2.0,'fillColor':c,'fillOpacity':.42}

def build_map(grid, incidents, origin=None, destination=None, fast=None, easy=None, tile=None, allow_grid_click=True, center=None):
    if center is None:
        if origin and destination: center=((origin[0]+destination[0])/2,(origin[1]+destination[1])/2)
        else:
            c=grid.geometry.unary_union.centroid; center=(c.y,c.x)
    mp=folium.Map(location=center,zoom_start=13,tiles=None,control_scale=True)
    if tile:
        folium.TileLayer(tiles=tile,attr='Offline cached map',name='OFFLINE MAP',overlay=False,control=False).add_to(mp)
    else:
        # Deliberately no remote tile source: offline operation remains possible.
        folium.TileLayer(tiles='CartoDB dark_matter',name='Online map (prep only)',overlay=False,control=False).add_to(mp)
    def style_fn(feature):
        return incident_style(feature['properties']['grid_id'],incidents)
    tooltip=folium.GeoJsonTooltip(fields=['grid_id'],aliases=['TILE'],sticky=True)
    popup=folium.GeoJsonPopup(fields=['grid_id'],aliases=['Emergency tile'],localize=True)
    folium.GeoJson(grid[['grid_id','geometry']].__geo_interface__,style_function=style_fn,highlight_function=lambda f:{'weight':2.5,'fillOpacity':.55},tooltip=tooltip,popup=popup,name='Emergency grid').add_to(mp)
    hz=grid[grid.grid_id.astype(int).isin([int(x) for x in incidents])]
    if len(hz):
        HeatMap([[r.geometry.centroid.y,r.geometry.centroid.x,float(incidents[str(int(r.grid_id))].get('severity',3))] for _,r in hz.iterrows()],radius=28,blur=22,min_opacity=.12).add_to(mp)
    if fast is not None and len(fast): folium.GeoJson(fast.__geo_interface__,style_function=lambda f:{'color':'#20c7ff','weight':4,'opacity':.55}).add_to(mp)
    if easy is not None and len(easy): folium.GeoJson(easy.__geo_interface__,style_function=lambda f:{'color':'#5bea79','weight':5,'opacity':.95}).add_to(mp)
    if origin: folium.CircleMarker(origin,radius=8,color='#5bea79',fill=True,fill_color='#5bea79',fill_opacity=1,popup='START').add_to(mp)
    if destination: folium.Marker(destination,icon=folium.DivIcon(html='<div style="font-size:24px;color:white">⚑</div>'),popup='DESTINATION').add_to(mp)
    return mp

def find_tile(grid, lat, lon):
    p=Point(float(lon),float(lat))
    hit=grid[grid.geometry.contains(p)]
    if len(hit): return int(hit.iloc[0].grid_id)
    # fallback to nearest tile centroid if the click is just outside a polygon edge
    d=grid.geometry.centroid.distance(p)
    if len(d): return int(grid.iloc[int(d.idxmin())].grid_id)
    return None

def decode_and_validate(code, manifest, grid):
    d=decode_sms(code)
    if d['package_id'] != manifest['package_id']:
        raise ValueError(f"Code belongs to package {d['package_id']}, but selected map is {manifest['package_id']}.")
    gh=manifest.get('grid_hash') or grid_fingerprint(grid)
    if d['grid_hash'] != gh: raise ValueError('Grid fingerprint mismatch. The emergency code and local map package are not compatible.')
    if d.get('expires_at'):
        try:
            if datetime.now(timezone.utc) > datetime.fromisoformat(d['expires_at']): raise ValueError('Emergency code has expired.')
        except ValueError as e:
            if 'expired' in str(e): raise
    return d

# -----------------------------------------------------------------------------
# HEADER
# -----------------------------------------------------------------------------
now=time.strftime('%H:%M')
st.markdown(f'''<div class="top"><div class="brand"><div class="shield">♜</div><div><div class="brandmain">EMERGENCY NAVIGATOR</div><div class="brandsub">COMMAND CENTER</div></div></div><div class="chip">● OFFLINE READY</div><div class="headcell"><div class="label">SYSTEM STATUS</div><div class="value" style="color:#5bea79">● OPERATIONAL</div></div><div class="headcell"><div class="label">MAP PACKAGES</div><div class="value">{len(packages())} LOADED</div></div><div class="headcell"><div class="label">TIME</div><div class="value">{now}</div></div></div>''',unsafe_allow_html=True)

mode_app=st.radio('APPLICATION MODE',['COMMAND CENTER','USER REPORT'],horizontal=True,index=0)

# -----------------------------------------------------------------------------
# SELECT PACKAGE
# -----------------------------------------------------------------------------
pkgs=packages()
if not pkgs:
    st.warning('No city package is loaded. Prepare a city while online first.')
    st.stop()

manifests=[json.loads((p/'manifest.json').read_text(encoding='utf-8')) for p in pkgs]
code_value=st.session_state.get('code_value','')
if mode_app=='COMMAND CENTER':
    left,center,right=st.columns([.9,2.6,1.05],gap='small')
else:
    left,center,right=st.columns([.95,2.5,1.0],gap='small')

# -----------------------------------------------------------------------------
# LEFT CONTROL PANEL
# -----------------------------------------------------------------------------
with left:
    st.markdown('<div class="card"><div class="title red">ROUTE CONTROL</div>',unsafe_allow_html=True)
    if mode_app=='COMMAND CENTER':
        code=st.text_area('EMERGENCY SMS CODE',value=code_value,placeholder='SMS3-...',height=90)
        c1,c2=st.columns(2)
        with c1:
            if st.button('DECODE CODE ↗',use_container_width=True):
                st.session_state.code_value=code.strip()
                st.session_state.decode_error=''
                try: st.session_state.decoded=decode_sms(code.strip())
                except Exception as e: st.session_state.decoded=None; st.session_state.decode_error=str(e)
        with c2:
            if st.button('CLEAR',use_container_width=True):
                for k in ['decoded','decode_error','selected_tile']: st.session_state.pop(k,None)
                st.rerun()
        if st.session_state.get('decode_error'): st.error(st.session_state.decode_error)
        d=st.session_state.get('decoded')
        if d: st.success(f"VALID CODE • {len(d['blocked_tiles'])} BLOCKED TILES")
    else:
        st.markdown('<div class="smallhelp">Click anywhere inside an emergency grid tile on the map. The selected tile becomes blocked locally and can be reported to the operator.</div>',unsafe_allow_html=True)
        d=None

    # Package selection / automatic SMS package match
    d=st.session_state.get('decoded') if mode_app=='COMMAND CENTER' else None
    pp=None
    if d:
        matches=[p for p,m in zip(pkgs,manifests) if m.get('package_id')==d.get('package_id')]
        if matches: pp=matches[0]
    if pp is None:
        labels=[m['city']+' ['+m.get('network','drive')+']' for m in manifests]
        sel=st.selectbox('OFFLINE CITY PACKAGE',labels)
        pp=pkgs[labels.index(sel)]
    manifest,graph,grid=load_package(str(pp))
    st.caption(f"PACKAGE: {manifest['package_id']} • {manifest['city']}")

    # Online preparation shortcut
    with st.expander('ONLINE MAP PREPARATION'):
        st.caption('Use this only while connected. The resulting package is then usable offline.')
        city_new=st.text_input('CITY / COUNTRY',placeholder='Ljubljana, Slovenia',key='prep_city')
        net_new=st.selectbox('NETWORK',['drive','bike','walk'],key='prep_net')
        if st.button('PREPARE CITY PACKAGE',use_container_width=True):
            import subprocess,sys
            if not city_new.strip(): st.warning('Enter a city first.')
            else:
                with st.spinner('Downloading OSM graph and creating emergency grid...'):
                    res=subprocess.run([sys.executable,'safesms_prepare_city.py','--city',city_new,'--network',net_new,'--hex-size','400'],capture_output=True,text=True)
                if res.returncode==0: st.success('City package prepared. Refresh the app.'); st.code(res.stdout[-1500:])
                else: st.error(res.stderr[-2500:])

# -----------------------------------------------------------------------------
# INCIDENT STATE
# -----------------------------------------------------------------------------
if 'incidents' not in st.session_state: st.session_state.incidents={}
if 'selected_tile' not in st.session_state: st.session_state.selected_tile=None

if mode_app=='COMMAND CENTER' and st.session_state.get('decoded'):
    d=st.session_state.decoded
    st.session_state.incidents={str(x['grid_id']):{'severity':x['severity'],'reason':x['reason']} for x in d['tiles']}

# -----------------------------------------------------------------------------
# CENTER MAP + ROUTING
# -----------------------------------------------------------------------------
with center:
    st.markdown(f'<div class="card"><div class="title">{manifest["city"].upper()} <span class="muted">• OFFLINE EMERGENCY MAP</span></div>',unsafe_allow_html=True)
    tile=tile_url(pp)
    incidents=st.session_state.incidents
    if mode_app=='USER REPORT':
        st.info('CLICK A TILE ON THE MAP TO REPORT AN INCIDENT. The tile will turn red immediately.')
    elif not incidents:
        st.caption('No blocked tiles loaded. Decode an emergency SMS or use the operator incident editor.')

    origin=st.session_state.get('origin')
    destination=st.session_state.get('destination')
    fast=ease=None
    d=st.session_state.get('decoded')
    if d and d.get('origin') and d.get('destination'):
        origin=d['origin']; destination=d['destination']
        st.session_state.origin=origin; st.session_state.destination=destination
    elif mode_app=='COMMAND CENTER':
        with st.expander('ROUTE ORIGIN / DESTINATION',expanded=False):
            o1,o2=st.columns(2)
            with o1:
                olat=st.number_input('Origin latitude',value=float(origin[0]) if origin else 46.05,format='%.6f')
                olon=st.number_input('Origin longitude',value=float(origin[1]) if origin else 14.50,format='%.6f')
            with o2:
                dlat=st.number_input('Destination latitude',value=float(destination[0]) if destination else 46.06,format='%.6f')
                dlon=st.number_input('Destination longitude',value=float(destination[1]) if destination else 14.51,format='%.6f')
            st.session_state.origin=(olat,olon); st.session_state.destination=(dlat,dlon)
            origin=st.session_state.origin; destination=st.session_state.destination

    # Build map first so clicks can select tiles.
    mp=build_map(grid,incidents,origin,destination,None,None,tile,allow_grid_click=True)
    map_out=st_folium(mp,width=None,height=600,key='safesms_map',returned_objects=['last_clicked'])
    clicked=map_out.get('last_clicked') if isinstance(map_out,dict) else None
    if clicked and mode_app=='USER REPORT':
        tid=find_tile(grid,clicked.get('lat'),clicked.get('lng'))
        if tid is not None:
            st.session_state.selected_tile=tid
            st.session_state.last_click=(clicked.get('lat'),clicked.get('lng'))
            st.rerun()

    # Routing after map interaction
    if d and origin and destination:
        try:
            on=ox.distance.nearest_nodes(graph,origin[1],origin[0]); dn=ox.distance.nearest_nodes(graph,destination[1],destination[0])
            safe_graph=blocked_graph(graph,grid,incidents.keys())
            fr=route(safe_graph,on,dn,'fastest',d.get('mode','vehicle'))
            er=route(safe_graph,on,dn,'easiest',d.get('mode','vehicle'))
            fast=route_gdf(safe_graph,fr); ease=route_gdf(safe_graph,er)
            # redraw with routes
            mp2=build_map(grid,incidents,origin,destination,fast,ease,tile,allow_grid_click=True)
            st_folium(mp2,width=None,height=600,key='safesms_route_map',returned_objects=[])
            st.markdown(f'<div class="metricrow"><div class="metric"><div class="label">FASTEST</div><div class="mval">{km(fast):.1f} km</div></div><div class="metric"><div class="label">EASIEST</div><div class="mval">{km(ease):.1f} km</div></div><div class="metric"><div class="label">BLOCKED TILES</div><div class="mval" style="color:#ff4c43">{len(incidents)}</div></div><div class="metric"><div class="label">MAP MODE</div><div class="mval" style="font-size:16px">OFFLINE</div></div></div>',unsafe_allow_html=True)
        except Exception as e:
            st.warning(f'Route could not be calculated for this incident state: {e}')
    st.markdown('</div>',unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# RIGHT PANEL
# -----------------------------------------------------------------------------
with right:
    if mode_app=='USER REPORT':
        st.markdown('<div class="card"><div class="title red">REPORT INCIDENT</div>',unsafe_allow_html=True)
        tid=st.session_state.get('selected_tile')
        if tid is None:
            st.markdown('<div class="smallhelp">No tile selected. Click a grid tile on the map.</div>',unsafe_allow_html=True)
        else:
            row=grid[grid.grid_id==tid].iloc[0]
            st.markdown(f'<div class="route"><div class="rtitle">TILE {tid}</div><div class="muted">Selected from map</div></div>',unsafe_allow_html=True)
            reason=st.selectbox('WHAT IS HAPPENING?',REASONS,index=0,key='user_reason')
            severity=st.slider('HOW DANGEROUS DOES IT FEEL?',1,5,3,key='user_severity')
            st.caption('1 = minor inconvenience • 5 = extreme danger')
            if st.button('BLOCK TILE',use_container_width=True):
                st.session_state.incidents[str(tid)]={'severity':severity,'reason':reason}
                st.success(f'Tile {tid} blocked: {reason} • severity {severity}/5')
                st.rerun()
            phone=st.text_input('OPERATOR PHONE NUMBER',placeholder='+38640123456',key='user_operator_phone')
            if st.button('SEND REPORT TO OPERATOR',use_container_width=True):
                if str(tid) not in st.session_state.incidents: st.session_state.incidents[str(tid)]={'severity':severity,'reason':reason}
                incidents=st.session_state.incidents
                code=encode_sms(manifest['city'],int(manifest['hex_size']),manifest.get('grid_hash',grid_fingerprint(grid)),incidents.keys(),incidents=incidents,network=manifest.get('network','drive'),mode='vehicle',preference='easiest')
                st.session_state.report_code=code
                if not phone: st.warning('Enter the operator phone number.')
                else:
                    try:
                        sid=send_sms_twilio(phone,code); st.success(f'SMS SENT • {sid}')
                    except Exception as e: st.error(str(e))
                st.code(code)
        st.markdown('</div>',unsafe_allow_html=True)
    else:
        st.markdown('<div class="route green"><div class="rtitle">SAFE / EASIEST ROUTE</div><div class="muted">Avoids blocked tiles and penalizes main-traffic road classes</div></div>',unsafe_allow_html=True)
        st.markdown('<div class="route"><div class="rtitle">DANGER ZONES HEATMAP</div><div class="muted">Severity-weighted emergency tile density</div></div>',unsafe_allow_html=True)

    # Operator incident editor
    if mode_app=='COMMAND CENTER':
        st.markdown('<div class="card"><div class="title">INCIDENT EDITOR</div>',unsafe_allow_html=True)
        st.markdown('<div class="smallhelp">Select a tile from the list or use USER REPORT mode to click the map. Every blocked tile carries its reason and severity into the SMS.</div>',unsafe_allow_html=True)
        tile_ids=[int(x) for x in grid.grid_id.tolist()]
        chosen=st.selectbox('TILE',tile_ids,key='operator_tile')
        reason=st.selectbox('REASON',REASONS,key='operator_reason')
        severity=st.slider('SEVERITY',1,5,3,key='operator_severity')
        if st.button('ADD / UPDATE BLOCKED TILE',use_container_width=True):
            st.session_state.incidents[str(chosen)]={'severity':severity,'reason':reason}; st.success(f'Tile {chosen} updated.'); st.rerun()
        if st.session_state.incidents:
            data=[{'Tile':int(k),'Severity':v['severity'],'Reason':v['reason']} for k,v in st.session_state.incidents.items()]
            st.dataframe(pd.DataFrame(data).sort_values('Tile'),hide_index=True,use_container_width=True)
        if st.button('CLEAR ALL BLOCKED TILES',use_container_width=True): st.session_state.incidents={}; st.rerun()
        st.markdown('</div>',unsafe_allow_html=True)

        # Generate + send emergency SMS
        st.markdown('<div class="card"><div class="title red">EMERGENCY SMS</div>',unsafe_allow_html=True)
        phone=st.text_input('RECIPIENT PHONE NUMBER',placeholder='+38640123456',key='operator_phone')
        pref=st.selectbox('ROUTE PROFILE',['easiest','fastest'],key='operator_pref')
        transport=st.selectbox('TRANSPORT',['vehicle','bicycle','walking'],key='operator_mode')
        expires=st.number_input('CODE VALID FOR (MINUTES)',min_value=5,max_value=1440,value=180,step=5)
        if st.button('GENERATE EMERGENCY CODE',use_container_width=True):
            try:
                code=encode_sms(manifest['city'],int(manifest['hex_size']),manifest.get('grid_hash',grid_fingerprint(grid)),st.session_state.incidents.keys(),origin,destination,transport,pref,manifest.get('network','drive'),st.session_state.incidents,datetime.now(timezone.utc).isoformat(),(datetime.now(timezone.utc)+timedelta(minutes=int(expires))).isoformat())
                st.session_state.generated_code=code
            except Exception as e: st.error(str(e))
        if st.session_state.get('generated_code'):
            st.code(st.session_state.generated_code)
            if st.button('SEND CODE VIA SMS',use_container_width=True):
                if not phone: st.warning('Enter a phone number.')
                else:
                    try:
                        sid=send_sms_twilio(phone,st.session_state.generated_code); st.success(f'SMS SENT • {sid}')
                    except Exception as e: st.error(str(e))
        st.markdown('</div>',unsafe_allow_html=True)

    if st.session_state.incidents:
        st.markdown('<div class="dangerbox">⚠ <b>{} BLOCKED TILES</b><br>Reasons and severity are encoded in the emergency SMS.</div>'.format(len(st.session_state.incidents)),unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# BOTTOM SUMMARY
# -----------------------------------------------------------------------------
st.markdown('---')
a,b,c,d=st.columns(4)
with a: st.metric('BLOCKED TILES',len(st.session_state.incidents))
with b: st.metric('SEVERE (4–5)',sum(1 for x in st.session_state.incidents.values() if int(x.get('severity',0))>=4))
with c: st.metric('OFFLINE PACKAGES',len(pkgs))
with d: st.metric('MAP STATUS','READY' if tile_url(pp) else 'GRID ONLY')
st.caption('SafeSMS demo • routing, emergency grid, incident state and SMS decoding are local. Internet is only required when preparing/downloading a city package or sending SMS through Twilio.')

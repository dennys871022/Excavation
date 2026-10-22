"""3D 開挖順序動畫（three.js）：一天一天播放，怪手到當天開挖的分區挖土、土車一台一台運出去。

資料來源是雲端 dispatch_logs（已排除作廢）：每天各分區出了多少方、台北港幾車次、FDA幾車次。
坑的深度與顏色跟「3D 渲染實景」相同規則（累計實挖方量 ÷ 分區面積；顏色沿用進度規則）。
由瀏覽器從 CDN 載入 three.js，伺服器端不用裝任何東西；只有選到這個模式才會執行。
"""
import json
import re

import pandas as pd
import streamlit.components.v1 as components

from render_3d import THREE_CDN
from zones import zone_progress_style


def _opaque(rgba):
    m = re.findall(r"[\d.]+", rgba)
    return f"rgb({m[0]},{m[1]},{m[2]})" if len(m) >= 3 else rgba


def build_dig_days(logs, df_zones, stage_idx, stage_label, max_days=400):
    """把派車紀錄整理成「一天一筆」的動畫資料。回傳 (zones_info, palette, days)。"""
    if logs is None or logs.empty or df_zones is None or df_zones.empty:
        return [], [], []
    d = logs.copy()
    d["_date"] = pd.to_datetime(d["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    d = d[d["_date"].notna()]
    d["_vol"] = pd.to_numeric(d.get("載運方量(m³)", 0), errors="coerce").fillna(0.0)
    d["_trips"] = pd.to_numeric(d.get("車次數", 1), errors="coerce").fillna(1)
    dest = d["運送目的地"].fillna("台北港").astype(str) if "運送目的地" in d.columns else pd.Series("台北港", index=d.index)
    d["_fda"] = dest.eq("FDA")
    zone_col = d["出土分區"].fillna("").astype(str) if "出土分區" in d.columns else pd.Series("", index=d.index)
    d["_zone"] = zone_col

    zrows, areas, totals = [], [], []
    for _, r in df_zones.iterrows():
        th = r.get("各階累計方量", []) or []
        area = float(r.get("區域面積(㎡)", 0) or 0)
        total = float(th[-1]) if len(th) else 0.0
        zrows.append(r); areas.append(area); totals.append(total)
    codes = [str(r["分區代號"]) for r in zrows]
    cum = {c: 0.0 for c in codes}
    palette, pidx, days = [], {}, []
    dates = sorted(d["_date"].unique())[-max_days:]
    # 如果被截掉前面的日子，要先把之前累計進去
    for dt_ in sorted(d["_date"].unique())[:-max_days]:
        g = d[d["_date"] == dt_]
        for z, v in g[g["_zone"].isin(codes)].groupby("_zone")["_vol"].sum().items():
            cum[z] += float(v)
    for dt_ in dates:
        g = d[d["_date"] == dt_]
        delta = g[g["_zone"].isin(codes)].groupby("_zone")["_vol"].sum().to_dict()
        for z, v in delta.items():
            cum[z] += float(v)
        dep, col, dl = [], [], []
        for k, r in enumerate(zrows):
            c = codes[k]
            text, rgba = zone_progress_style(r, cum[c], r.get("各階累計方量", []), stage_idx, stage_label)
            rgb = _opaque(rgba)
            if rgb not in pidx:
                pidx[rgb] = len(palette); palette.append(rgb)
            dep.append(round(min(cum[c], totals[k]) / areas[k], 3) if areas[k] > 0 and totals[k] > 0 else 0.0)
            col.append(pidx[rgb])
            dl.append(round(float(delta.get(c, 0.0)), 1))
        days.append({
            "date": dt_, "trips": int(g.loc[~g["_fda"], "_trips"].sum()), "fda": int(g.loc[g["_fda"], "_trips"].sum()),
            "vol": round(float(g["_vol"].sum()), 1), "cum": round(float(sum(cum.values())), 1),
            "dep": dep, "col": col, "dl": dl,
        })
    zinfo = [{"c": codes[k], "x0": float(r["x_min"]), "x1": float(r["x_max"]), "y0": float(r["y_min"]), "y1": float(r["y_max"])}
             for k, r in enumerate(zrows)]
    return zinfo, palette, days


_HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
 html,body{margin:0;height:100%;overflow:hidden;background:#bfd9f0;font-family:'Segoe UI','Microsoft JhengHei',sans-serif}
 #wrap{position:relative;width:100%;height:100%}
 canvas{display:block}
 #title{position:absolute;left:12px;top:10px;background:rgba(255,255,255,.85);padding:6px 12px;border-radius:6px;font-weight:700;color:#1f3a5f;font-size:14px}
 #info{position:absolute;right:12px;top:10px;background:rgba(20,30,45,.82);color:#fff;padding:10px 14px;border-radius:8px;font-size:13px;line-height:1.7;min-width:190px}
 #info b{font-size:18px;color:#ffd24d}
 #bar{position:absolute;left:12px;right:12px;bottom:10px;background:rgba(255,255,255,.9);border-radius:8px;padding:8px 12px;display:flex;gap:10px;align-items:center;font-size:13px}
 #bar button,#bar select{border:0;background:#1f3a5f;color:#fff;border-radius:5px;padding:6px 12px;font-size:13px;cursor:pointer}
 #bar select{padding:5px 6px}
 #sl{flex:1}
 #err{position:absolute;left:20px;top:60px;color:#b00;font-size:14px}
</style>
<script type="importmap">{"imports":{"three":"__BASE__/build/three.module.js","three/addons/":"__BASE__/examples/jsm/"}}</script>
</head><body><div id="wrap"><div id="title"></div><div id="info"></div>
<div id="bar"><button id="play">⏸ 暫停</button><button id="rst">⏮ 重播</button>
<span>速度</span><select id="spd"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select>
<input id="sl" type="range" min="0" max="0" value="0" step="1"><span id="dl"></span></div><div id="err"></div></div>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const D=__DATA__;
document.getElementById('title').textContent=D.title;
try{
const zs=D.zones,days=D.days,zex=D.zex,pal=D.palette.map(s=>{const m=s.match(/[\d.]+/g);return [m[0]/255,m[1]/255,m[2]/255];});
const nz=zs.length,nd=days.length;
let X0=1e18,X1=-1e18,Y0=1e18,Y1=-1e18;
zs.forEach(z=>{X0=Math.min(X0,z.x0);X1=Math.max(X1,z.x1);Y0=Math.min(Y0,z.y0);Y1=Math.max(Y1,z.y1);});
const W=X1-X0,H=Y1-Y0,size=Math.max(W,H),cx=(X0+X1)/2,cy=(Y0+Y1)/2,step=size/170,pad=size*0.12;
const gx0=X0-pad,gy0=Y0-pad,nx=Math.ceil((W+2*pad)/step),ny=Math.ceil((H+2*pad)/step),NV=(nx+1)*(ny+1);
const zi=new Int16Array(NV).fill(-1);
zs.forEach((z,k)=>{
  const i0=Math.max(0,Math.ceil((z.x0-gx0)/step)),i1=Math.min(nx,Math.ceil((z.x1-gx0)/step)-1);
  const j0=Math.max(0,Math.ceil((z.y0-gy0)/step)),j1=Math.min(ny,Math.ceil((z.y1-gy0)/step)-1);
  for(let j=j0;j<=j1;j++)for(let i=i0;i<=i1;i++)zi[j*(nx+1)+i]=k;
  z.wx=(z.x0+z.x1)/2-cx;z.wz=-((z.y0+z.y1)/2-cy);
});
const hash=(a,b)=>{const s=Math.sin(a*127.1+b*311.7)*43758.5453;return s-Math.floor(s);};
const vn=(x,y)=>{const xi=Math.floor(x),yi=Math.floor(y),xf=x-xi,yf=y-yi,u=xf*xf*(3-2*xf),v=yf*yf*(3-2*yf);
 return (hash(xi,yi)*(1-u)+hash(xi+1,yi)*u)*(1-v)+(hash(xi,yi+1)*(1-u)+hash(xi+1,yi+1)*u)*v;};
const fbm=(x,y)=>0.5*vn(x,y)+0.3*vn(x*2.3,y*2.3)+0.2*vn(x*5.1,y*5.1);
const noise=new Float32Array(NV);
for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++)noise[j*(nx+1)+i]=0.82+0.36*fbm((gx0+i*step)*0.9,(gy0+j*step)*0.9);
const pos=new Float32Array(NV*3),col=new Float32Array(NV*3),hts=new Float32Array(NV);
for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++){const n=j*(nx+1)+i;pos[n*3]=gx0+i*step-cx;pos[n*3+2]=-(gy0+j*step-cy);}
const idx=[];for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){const a=j*(nx+1)+i,b=a+1,c=a+nx+1,d=c+1;idx.push(a,b,c,b,d,c);}
const geo=new THREE.BufferGeometry();
geo.setAttribute('position',new THREE.BufferAttribute(pos,3));geo.setAttribute('color',new THREE.BufferAttribute(col,3));
geo.setIndex(idx);
const strata=d=>d<1?[.33,.22,.12]:d<3?[.62,.46,.28]:d<6?[.55,.5,.42]:[.45,.45,.46];
const ground=[.62,.55,.4];
// 目前（插值後）每區深度與顏色
const curDep=new Float32Array(nz),curCol=new Array(nz).fill(0);
function updateTerrain(){
  for(let n=0;n<NV;n++){const k=zi[n];hts[n]=k>=0?-curDep[k]*zex:0;}
  for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++){
    const n=j*(nx+1)+i;pos[n*3+1]=hts[n];
    let hm=0;
    if(i>0)hm=Math.max(hm,Math.abs(hts[n-1]-hts[n]));if(i<nx)hm=Math.max(hm,Math.abs(hts[n+1]-hts[n]));
    if(j>0)hm=Math.max(hm,Math.abs(hts[n-nx-1]-hts[n]));if(j<ny)hm=Math.max(hm,Math.abs(hts[n+nx+1]-hts[n]));
    const k=zi[n];let c;
    if(hm>step*0.4)c=strata(-hts[n]/zex);else if(k>=0&&curDep[k]>0.01)c=pal[curCol[k]];else c=ground;
    const f=noise[n];col[n*3]=Math.min(1,c[0]*f);col[n*3+1]=Math.min(1,c[1]*f);col[n*3+2]=Math.min(1,c[2]*f);
  }
  geo.attributes.position.needsUpdate=true;geo.attributes.color.needsUpdate=true;geo.computeVertexNormals();
}
// ---- 場景 ----
const wrap=document.getElementById('wrap');
const renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(2,window.devicePixelRatio||1));
renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=0.82;wrap.insertBefore(renderer.domElement,wrap.firstChild);
const scene=new THREE.Scene(),sky=new THREE.Color(0xbfd9f0);scene.background=sky;scene.fog=new THREE.Fog(sky,size*2.2,size*6);
const camera=new THREE.PerspectiveCamera(42,1,size*0.01,size*12);camera.position.set(size*0.42,size*0.6,size*0.8);
const controls=new OrbitControls(camera,renderer.domElement);controls.target.set(0,-1,0);controls.enableDamping=true;controls.maxPolarAngle=Math.PI/2-0.03;
scene.add(new THREE.HemisphereLight(0xdfeeff,0x8a7a5a,0.7));
const sun=new THREE.DirectionalLight(0xfff3dc,2.0);sun.position.set(size*0.7,size*0.9,size*0.35);sun.castShadow=true;
sun.shadow.mapSize.set(2048,2048);const sc=sun.shadow.camera,R0=size*0.85;sc.left=-R0;sc.right=R0;sc.top=R0;sc.bottom=-R0;sc.near=1;sc.far=size*4;sun.shadow.bias=-0.0004;sun.shadow.normalBias=0.05;scene.add(sun);
const terrain=new THREE.Mesh(geo,new THREE.MeshStandardMaterial({vertexColors:true,roughness:.96,metalness:0}));terrain.castShadow=true;terrain.receiveShadow=true;scene.add(terrain);
{const hx0=gx0-cx+step*.6,hx1=gx0+nx*step-cx-step*.6,hy0=-(gy0+ny*step-cy)+step*.6,hy1=-(gy0-cy)-step*.6,R=size*7;
 const shp=new THREE.Shape();shp.moveTo(-R,-R);shp.lineTo(R,-R);shp.lineTo(R,R);shp.lineTo(-R,R);shp.lineTo(-R,-R);
 const hole=new THREE.Path();hole.moveTo(hx0,hy0);hole.lineTo(hx0,hy1);hole.lineTo(hx1,hy1);hole.lineTo(hx1,hy0);hole.lineTo(hx0,hy0);shp.holes.push(hole);
 const far=new THREE.Mesh(new THREE.ShapeGeometry(shp),new THREE.MeshStandardMaterial({color:0x8f8466,roughness:1}));far.rotation.x=-Math.PI/2;far.position.y=-.01;far.receiveShadow=true;scene.add(far);}
function label(t){const cv=document.createElement('canvas');cv.width=128;cv.height=48;const g=cv.getContext('2d');g.font='bold 30px sans-serif';g.textAlign='center';g.textBaseline='middle';
 g.lineWidth=6;g.strokeStyle='rgba(255,255,255,.9)';g.strokeText(t,64,24);g.fillStyle='#1b1b1b';g.fillText(t,64,24);
 const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(cv),transparent:true}));sp.scale.set(size*0.055,size*0.021,1);return sp;}
const labels=zs.map(z=>{const s=label(z.c);scene.add(s);return s;});
// ---- 便道（出口在西側）----
const gate={x:gx0-cx-pad*0.1,z:0},beyond={x:gate.x-size*0.45,z:0};
{const road=new THREE.Mesh(new THREE.PlaneGeometry(size*0.5,size*0.05),new THREE.MeshStandardMaterial({color:0x6b6b6b,roughness:1}));
 road.rotation.x=-Math.PI/2;road.position.set(gate.x-size*0.25,0.03,0);road.receiveShadow=true;scene.add(road);}
// ---- 怪手 ----
const mk=c=>new THREE.MeshStandardMaterial({color:c,roughness:.6,metalness:.2});
const ex=new THREE.Group(),exBody=new THREE.Group();ex.add(exBody);
{const tr=new THREE.Mesh(new THREE.BoxGeometry(3.4,.7,1.3),mk(0x222222));tr.position.y=.35;ex.add(tr);
 const bd=new THREE.Mesh(new THREE.BoxGeometry(2.2,1.1,1.5),mk(0xf1c40f));bd.position.y=1.25;exBody.add(bd);
 const cab=new THREE.Mesh(new THREE.BoxGeometry(.9,.9,1.2),mk(0x6fa8dc));cab.position.set(-.5,2.2,0);exBody.add(cab);}
const boom=new THREE.Group();boom.position.set(.9,1.8,0);exBody.add(boom);
const b1=new THREE.Mesh(new THREE.BoxGeometry(2.8,.38,.4),mk(0xf1c40f));b1.position.x=1.4;boom.add(b1);
const stick=new THREE.Group();stick.position.set(2.8,0,0);boom.add(stick);
const b2=new THREE.Mesh(new THREE.BoxGeometry(.3,2.0,.3),mk(0xe0b000));b2.position.y=-1.0;stick.add(b2);
const bucket=new THREE.Mesh(new THREE.BoxGeometry(.9,.5,.8),mk(0x444444));bucket.position.y=-2.1;stick.add(bucket);
ex.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});
const vs=Math.max(1,size/70);ex.scale.setScalar(vs);scene.add(ex);
// 便道斜坡（怪手所在位置 → 坑口）
const ramp=new THREE.Mesh(new THREE.BoxGeometry(1,.25,3.5),new THREE.MeshStandardMaterial({color:0x7a5a38,roughness:1}));ramp.castShadow=true;ramp.receiveShadow=true;ramp.visible=false;scene.add(ramp);
// ---- 土車 ----
const MAXT=14;
function truck(color){const g=new THREE.Group();
 const cab=new THREE.Mesh(new THREE.BoxGeometry(1.6,1.6,1.9),mk(0x2c3e50));cab.position.set(2.6,1.2,0);g.add(cab);
 const bed=new THREE.Mesh(new THREE.BoxGeometry(4.2,1.3,2.1),mk(color));bed.position.set(-.3,1.35,0);g.add(bed);
 const load=new THREE.Mesh(new THREE.BoxGeometry(3.8,.5,1.8),mk(0x6b4a2b));load.position.set(-.3,2.15,0);g.add(load);
 const ch=new THREE.Mesh(new THREE.BoxGeometry(6,.4,1.6),mk(0x222222));ch.position.y=.45;g.add(ch);
 g.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});g.scale.setScalar(vs*0.9);g.visible=false;scene.add(g);return g;}
const trucks=[];for(let i=0;i<MAXT*2;i++)trucks.push({g:truck(i<MAXT?0xe67e22:0x3498db),on:false});
// ---- 時間軸 ----
let T=0,playing=true,speed=1,lastDay=-1;const DAYSEC=4.0,LIFE=0.8;
const sl=document.getElementById('sl'),info=document.getElementById('info'),dl=document.getElementById('dl'),playBtn=document.getElementById('play');
sl.max=Math.max(0,nd-1);dl.textContent=nd+' 天';
const sm=t=>t<=0?0:t>=1?1:t*t*(3-2*t);
// 每天的挖掘目標（依當天方量大小排序，最多3區）
const targets=days.map(d=>{const a=[];d.dl.forEach((v,k)=>{if(v>0)a.push({k,v});});a.sort((p,q)=>q.v-p.v);const t=a.slice(0,3),s=t.reduce((x,y)=>x+y.v,0);let acc=0;
  return t.map(o=>{const w=o.v/s;const r={k:o.k,a:acc,b:acc+w};acc+=w;return r;});});
// 怪手所在（以前一次的目標為準）
let exK=-1,exX=0,exZ=0;
for(let i=0;i<nd&&exK<0;i++)if(targets[i].length){exK=targets[i][0].k;}
if(exK>=0){exX=zs[exK].wx;exZ=zs[exK].wz;}
const dirTo=(x,z)=>{const dx=gate.x-x,dz=gate.z-z,l=Math.hypot(dx,dz)||1;return [dx/l,dz/l];};
function depthAt(wx,wz){const i=Math.round((wx+cx-gx0)/step),j=Math.round((-wz+cy-gy0)/step);if(i<0||j<0||i>nx||j>ny)return 0;return -hts[j*(nx+1)+i];}
function floorY(){return -(exK>=0?curDep[exK]:0)*zex;}
function sched(i){ // 第 i 天的土車出發時間（天為單位）
  const d=days[i],n=Math.min(d.trips,MAXT),m=Math.min(d.fda,MAXT),out=[];
  for(let a=0;a<n;a++)out.push({t:i+(a+.5)/n*0.85,fda:false,slot:a});
  for(let a=0;a<m;a++)out.push({t:i+(a+.5)/m*0.85+0.04,fda:true,slot:MAXT+a});
  return out;}
const live=new Map();
function updateTrucks(){
  const di=Math.floor(T);
  for(const k of Array.from(live.keys())){const o=live.get(k);if(T-o.t>LIFE||T<o.t-0.01){trucks[o.slot].g.visible=false;live.delete(k);}}
  for(let i=Math.max(0,di-1);i<=Math.min(nd-1,di);i++)for(const s of sched(i)){
    const u=(T-s.t)/LIFE;if(u<0||u>1)continue;const key=i+'_'+s.slot;
    if(!live.has(key)){const [dx,dz]=dirTo(exX,exZ);const fy=floorY();
      const L=Math.max(8,Math.abs(fy)*3.2);const rim=Math.min(L,Math.hypot(gate.x-exX,gate.z-exZ)*0.9);
      live.set(key,{t:s.t,slot:s.slot,sx:exX,sz:exZ,sy:fy,dx,dz,L:rim});}
    const o=live.get(key),tk=trucks[o.slot].g;tk.visible=true;
    const ramp_end={x:o.sx+o.dx*o.L,z:o.sz+o.dz*o.L};
    const seg1=o.L,seg2=Math.hypot(gate.x-ramp_end.x,gate.z-ramp_end.z),seg3=Math.hypot(beyond.x-gate.x,beyond.z-gate.z),tot=seg1+seg2+seg3;
    let d=sm(u)*tot,x,z,y;
    if(d<seg1){const q=d/seg1;x=o.sx+o.dx*o.L*q;z=o.sz+o.dz*o.L*q;y=o.sy*(1-q)+0.15;}
    else if(d<seg1+seg2){const q=(d-seg1)/seg2;x=ramp_end.x+(gate.x-ramp_end.x)*q;z=ramp_end.z+(gate.z-ramp_end.z)*q;y=0.1;}
    else{const q=(d-seg1-seg2)/seg3;x=gate.x+(beyond.x-gate.x)*q;z=gate.z+(beyond.z-gate.z)*q;y=0.1;}
    tk.position.set(x,y,z);tk.rotation.y=Math.atan2(o.dx,o.dz)-Math.PI/2+(d>=seg1?Math.atan2(-(gate.z-ramp_end.z)*0,1):0);
    // 車頭朝行進方向（模型車頭在 +x）
    const ang=Math.atan2(-(d<seg1?o.dz:(d<seg1+seg2?(gate.z-ramp_end.z)/(seg2||1):0)),(d<seg1?o.dx:(d<seg1+seg2?(gate.x-ramp_end.x)/(seg2||1):-1)));
    tk.rotation.y=ang;
  }
}
function setDayState(){
  const di=Math.min(nd-1,Math.floor(T)),p=Math.min(1,T-di);
  const prev=di>0?days[di-1].dep:null,cur=days[di].dep,e=sm(p/0.85);
  for(let k=0;k<nz;k++){const a=prev?prev[k]:0;curDep[k]=a+(cur[k]-a)*e;curCol[k]=(p>0.5||!prev)?days[di].col[k]:days[di-1].col[k];}
  // 怪手：依當天目標分區依序移動
  const tg=targets[di];
  if(tg.length){let seg=tg[tg.length-1];for(const s of tg){if(p<=s.b){seg=s;break;}}
    const pk=seg.k,lp=Math.min(1,Math.max(0,(p-seg.a)/(seg.b-seg.a||1))),mv=sm(lp/0.25);
    const tx=zs[pk].wx,tz=zs[pk].wz;
    if(exK!==pk&&mv>=1){exK=pk;exX=tx;exZ=tz;}
    if(exK<0){exK=pk;exX=tx;exZ=tz;}
    else if(exK!==pk){exX=exX+(tx-exX)*Math.min(1,mv*0.12+0.0);exZ=exZ+(tz-exZ)*Math.min(1,mv*0.12);}
  }
  const fy=floorY();ex.position.set(exX,fy,exZ);
  const [dx,dz]=dirTo(exX,exZ);ex.rotation.y=Math.atan2(-dz,dx);
  const dig=tg.length&&p<0.9,tt=performance.now()/1000;
  boom.rotation.z=dig?-0.35+0.28*Math.sin(tt*3.2):-0.2;stick.rotation.z=dig?0.5+0.3*Math.sin(tt*3.2+1.2):0.3;exBody.rotation.y=dig?0.4*Math.sin(tt*1.6):0;
  // 便道斜坡
  const L=Math.max(8,Math.abs(fy)*3.2),rim=Math.min(L,Math.hypot(gate.x-exX,gate.z-exZ)*0.9);
  if(Math.abs(fy)>0.3&&dig){const ex2=exX+dx*rim,ez2=exZ+dz*rim,len=Math.hypot(rim,fy);
    ramp.visible=true;ramp.scale.set(len,1,1);ramp.position.set((exX+ex2)/2,fy/2+0.05,(exZ+ez2)/2);
    ramp.rotation.set(0,Math.atan2(-dz,dx),Math.atan2(-fy,rim)*-1,'YXZ');ramp.rotation.z=Math.atan2(-fy,rim);}
  else ramp.visible=false;
  labels.forEach((s,k)=>s.position.set(zs[k].wx,-curDep[k]*zex+size*0.012,zs[k].wz));
  if(di!==lastDay||p<0.9){updateTerrain();lastDay=di;}
  const d=days[di];
  const names=tg.map(s=>zs[s.k].c).join('、')||'（當天紀錄未指定分區）';
  info.innerHTML=`<b>${d.date}</b>　第 ${di+1}/${nd} 天<br>本日車次：${d.trips} 台${d.fda?`（另 FDA ${d.fda} 台）`:''}<br>本日方量：${d.vol.toLocaleString()} m³<br>累計方量：${d.cum.toLocaleString()} m³<br>挖掘分區：${names}`;
  sl.value=di;
}
function resize(){const w=wrap.clientWidth,h=wrap.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}
window.addEventListener('resize',resize);resize();
playBtn.onclick=()=>{if(!playing&&T>=nd-0.001){T=0;lastDay=-1;live.forEach(o=>trucks[o.slot].g.visible=false);live.clear();}playing=!playing;playBtn.textContent=playing?'⏸ 暫停':'▶ 播放';};
document.getElementById('rst').onclick=()=>{T=0;lastDay=-1;live.forEach(o=>trucks[o.slot].g.visible=false);live.clear();playing=true;playBtn.textContent='⏸ 暫停';};
document.getElementById('spd').onchange=e=>speed=parseFloat(e.target.value);
sl.oninput=e=>{T=parseInt(e.target.value);lastDay=-1;live.forEach(o=>trucks[o.slot].g.visible=false);live.clear();};
if(nd===0){document.getElementById('err').textContent='沒有可播放的出土紀錄';}
else{
  let last=performance.now();
  (function loop(now){
    const dt=Math.min(0.1,(now-last)/1000);last=now;
    if(playing){T+=dt*speed/DAYSEC;if(T>=nd-0.001){T=nd-0.001;playing=false;playBtn.textContent='▶ 重播';}}
    setDayState();updateTrucks();controls.update();renderer.render(scene,camera);requestAnimationFrame(loop);
  })(last);
}
window.__ready=true;
}catch(e){document.getElementById('err').textContent='3D 動畫失敗：'+e;}
</script></body></html>"""


def render_animation(zinfo, palette, days, title, z_exag, height=720, base_url=THREE_CDN):
    data = json.dumps({"zones": zinfo, "palette": palette, "days": days, "zex": float(z_exag), "title": title}, ensure_ascii=False)
    html = _HTML.replace("__BASE__", base_url).replace("__DATA__", data)
    components.html(html, height=height, scrolling=False)
    return html

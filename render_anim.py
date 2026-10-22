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
    """把派車紀錄整理成「一天一筆」的動畫資料。回傳 (zones_info, palette, days, slab_day)。
    slab_day＝第一挖（各分區第1階累計方量）全部挖完的那一天的序號（0 起算），沒挖完則為 None。"""
    if logs is None or logs.empty or df_zones is None or df_zones.empty:
        return [], [], [], None
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
    th0 = [float((r.get("各階累計方量", []) or [0])[0]) if len(r.get("各階累計方量", []) or []) else 0.0 for r in zrows]
    th0_sum = sum(th0)
    palette, pidx, days, slab_day = [], {}, [], None
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
        if slab_day is None and th0_sum > 0 and sum(min(cum[codes[k]], th0[k]) for k in range(len(codes))) >= 0.995 * th0_sum:
            slab_day = len(days)
        days.append({
            "date": dt_, "trips": int(g.loc[~g["_fda"], "_trips"].sum()), "fda": int(g.loc[g["_fda"], "_trips"].sum()),
            "vol": round(float(g["_vol"].sum()), 1), "cum": round(float(sum(cum.values())), 1),
            "dep": dep, "col": col, "dl": dl,
        })
    zinfo = [{"c": codes[k], "x0": float(r["x_min"]), "x1": float(r["x_max"]), "y0": float(r["y_min"]), "y1": float(r["y_max"])}
             for k, r in enumerate(zrows)]
    return zinfo, palette, days, slab_day


def default_site(df_zones):
    """歷建、地磅站、洗車台、警衛室、大門、便道、第一階段土堆的預設位置（圖資座標，公尺）。
    依現場說明推算：大門在西側（唯一出入口），門外是南北向馬路，土車左轉進場、出場右轉（往北）；
    第一階段土方先堆在 D'9‥D'14／E'9‥E'14 圍起來的範圍（約1.5層樓高），土車直接在土堆裝土，
    再上地磅站 → 洗車台 → 出大門；二挖以後在復工版上繞行再過磅。
    位置不準的話改這裡的數字即可（或把正確座標給我）。"""
    z = {str(r["分區代號"]): r for _, r in df_zones.iterrows()}
    X0, X1 = float(df_zones["x_min"].min()), float(df_zones["x_max"].max())
    Y0, Y1 = float(df_zones["y_min"].min()), float(df_zones["y_max"].max())
    size = max(X1 - X0, Y1 - Y0)

    def g(code, col, default):
        return float(z[code][col]) if code in z else default
    nx0 = g("C3", "x_max", X0 + 0.35 * (X1 - X0))          # 歷建所在缺口：左緣
    nx1 = g("E'7", "x_min", nx0 + 27.6)                     # 右緣
    ny1 = g("B4", "y_min", Y1 - 0.4 * (Y1 - Y0))            # 上緣
    ny0 = g("E3", "y_min", Y0 + 0.2 * (Y1 - Y0))            # 下緣
    hx, hy = (nx0 + nx1) / 2, (ny0 + ny1) / 2
    hw, hd, fence = min(9.0, (nx1 - nx0) - 12), min(14.0, (ny1 - ny0) - 12), 2.5
    west = df_zones[df_zones["x_min"] <= X0 + 1.0]
    gy = float((west["y_min"].min() + west["y_max"].max()) / 2) if len(west) else (Y0 + Y1) / 2
    x_w = X0 - 5.0
    gx = x_w - 11.0                                          # 大門（南北向門線，在洗車台西側）
    # 第一階段土堆範圍：D'9、D'14、E'9、E'14 四區圍起來
    mx0 = g("D'9", "x_min", X0 + 0.5 * (X1 - X0)); mx1 = g("D'14", "x_max", mx0 + 55.0)
    my0 = g("E'9", "y_min", Y0 + 0.1 * (Y1 - Y0)); my1 = g("D'9", "y_max", my0 + 18.6)
    y_w = my0 - 4.0                                          # 土堆南側車道（西行，過磅在這條）
    y_e = Y0 - 3.0                                           # 最南側車道（東行）
    wx = nx0 - 20.0                                          # 地磅站中心（歷建西南側）
    return {
        "hist": [hx - hw / 2, hx + hw / 2, hy - hd / 2, hy + hd / 2], "fence": fence,
        "ring": [nx0 - 8.0, nx1 + 8.0, y_w, ny1 + 5.0],      # 繞歷建＝走開挖區（鋪鐵板）：x0,x1,下緣,上緣
        "gate": [gx, gy], "road_x": gx - 12.0, "lane_in": gy - 1.8, "lane_out": gy + 1.8,
        "gate_z": [gy - 14.5, gy + 4.5], "guard": [gx + 5.5, gy + 7.5],
        "wash": [x_w - 9.0, x_w + 3.6, gy - 17.7, gy - 9.7],
        "x_w": x_w, "y_w": y_w, "y_e": y_e,
        "weigh": [wx - 9.0, wx + 9.0, y_w - 3.0, y_w + 3.0],
        "mound": [mx0, mx1, my0, my1], "mound_h": 5.0,
        "x_u": mx1 + 4.0, "x_f": g("F'16", "x_max", X1) - 3.0, "x_load": mx1 - 8.0,
        "X0": X0, "X1": X1, "Y0": Y0, "Y1": Y1,
    }


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
<input id="sl" type="range" min="0" max="0" value="0" step="1"><span id="dl"></span>
<label><input type="checkbox" id="ckHist" checked>歷建</label>
<span>復工版</span><select id="slabMode"><option value="auto" selected>第一挖完成後</option><option value="on">一律顯示</option><option value="off">隱藏</option></select></div><div id="err"></div></div>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const D=__DATA__;
document.getElementById('title').textContent=D.title;
try{
const S=D.site,slabDay=D.slabDay,zs=D.zones,days=D.days,zex=D.zex,pal=D.palette.map(s=>{const m=s.match(/[\d.]+/g);return [m[0]/255,m[1]/255,m[2]/255];});
const nz=zs.length,nd=days.length;
let X0=1e18,X1=-1e18,Y0=1e18,Y1=-1e18;
zs.forEach(z=>{X0=Math.min(X0,z.x0);X1=Math.max(X1,z.x1);Y0=Math.min(Y0,z.y0);Y1=Math.max(Y1,z.y1);});
const W=X1-X0,H=Y1-Y0,size=Math.max(W,H),cx=(X0+X1)/2,cy=(Y0+Y1)/2,step=size/170,pad=size*0.24;
const gx0=X0-pad,gy0=Y0-pad,nx=Math.ceil((W+2*pad)/step),ny=Math.ceil((H+2*pad)/step),NV=(nx+1)*(ny+1);
const zi=new Int16Array(NV).fill(-1);
const Wd=(x,y)=>[x-cx,-(y-cy)];
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
const slabMask=new Uint8Array(NV);
{const f=S.fence;for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++){const n=j*(nx+1)+i;if(zi[n]>=0)continue;
  const X=gx0+i*step,Y=gy0+j*step;
  if(X>=S.hist[0]-f&&X<=S.hist[1]+f&&Y>=S.hist[2]-f&&Y<=S.hist[3]+f)continue;
  slabMask[n]=1;}}
const pos=new Float32Array(NV*3),col=new Float32Array(NV*3),hts=new Float32Array(NV);
for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++){const n=j*(nx+1)+i;pos[n*3]=gx0+i*step-cx;pos[n*3+2]=-(gy0+j*step-cy);}
const idx=[];for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){const a=j*(nx+1)+i,b=a+1,c=a+nx+1,d=c+1;idx.push(a,b,c,b,d,c);}
const geo=new THREE.BufferGeometry();
geo.setAttribute('position',new THREE.BufferAttribute(pos,3));geo.setAttribute('color',new THREE.BufferAttribute(col,3));
geo.setIndex(idx);
const strata=d=>d<1?[.33,.22,.12]:d<3?[.62,.46,.28]:d<6?[.55,.5,.42]:[.45,.45,.46];
const ground=[.62,.55,.4];
// 目前（插值後）每區深度與顏色
const curDep=new Float32Array(nz),curCol=new Array(nz).fill(0);let slabCur=0;const SLAB_H=0.35,slabCol=[.98,.72,.04];
function updateTerrain(){
  for(let n=0;n<NV;n++){const k=zi[n];hts[n]=k>=0?-curDep[k]*zex:(slabMask[n]?SLAB_H*slabCur:0);}
  for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++){
    const n=j*(nx+1)+i;pos[n*3+1]=hts[n];
    let hm=0;
    if(i>0)hm=Math.max(hm,Math.abs(hts[n-1]-hts[n]));if(i<nx)hm=Math.max(hm,Math.abs(hts[n+1]-hts[n]));
    if(j>0)hm=Math.max(hm,Math.abs(hts[n-nx-1]-hts[n]));if(j<ny)hm=Math.max(hm,Math.abs(hts[n+nx+1]-hts[n]));
    const k=zi[n];let c;
    if(k<0&&slabMask[n]&&slabCur>0.02){const i2=n%(nx+1),j2=Math.floor(n/(nx+1)),st=(((i2+j2)>>2)&1)?0.88:1,m=slabCur;
      c=[ground[0]*(1-m)+slabCol[0]*st*m,ground[1]*(1-m)+slabCol[1]*st*m,ground[2]*(1-m)+slabCol[2]*st*m];}
    else if(hm>step*0.4)c=strata(-hts[n]/zex);else if(k>=0&&curDep[k]>0.01)c=pal[curCol[k]];else c=ground;
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
function label(t){const cv=document.createElement('canvas');cv.width=256;cv.height=64;const g=cv.getContext('2d');g.font='bold 34px sans-serif';g.textAlign='center';g.textBaseline='middle';
 g.lineWidth=7;g.strokeStyle='rgba(255,255,255,.92)';g.strokeText(t,128,32);g.fillStyle='#1b1b1b';g.fillText(t,128,32);
 const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(cv),transparent:true}));sp.scale.set(size*0.09,size*0.0225,1);return sp;}
const labels=zs.map(z=>{const s=label(z.c);scene.add(s);return s;});
// ---- 場區設施：大門(西側唯一出入口)、洗車台、警衛室、地磅站、歷建、便道、第一階段土堆 ----
const box=(w,h,d,color,x,y,z,sh=true)=>{const m=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),new THREE.MeshStandardMaterial({color,roughness:.85}));m.position.set(x,y,z);if(sh){m.castShadow=true;m.receiveShadow=true;}scene.add(m);return m;};
const Pw=(x,y)=>[x-cx,-(y-cy)];
const [gX,gZ]=Pw(S.gate[0],S.gate[1]),roadX=S.road_x-cx,zIn=-(S.lane_in-cy),zOut=-(S.lane_out-cy),xW=S.x_w-cx,zW=-(S.y_w-cy),zE=-(S.y_e-cy),roadZ=zE;
const gzN=-(S.gate_z[1]-cy),gzS=-(S.gate_z[0]-cy),[guX,guZ]=Pw(S.guard[0],S.guard[1]);
const wc=Pw((S.weigh[0]+S.weigh[1])/2,(S.weigh[2]+S.weigh[3])/2),wW=S.weigh[1]-S.weigh[0],wD=S.weigh[3]-S.weigh[2];
const hcx=(S.hist[0]+S.hist[1])/2-cx,hcz=-((S.hist[2]+S.hist[3])/2-cy),hW=S.hist[1]-S.hist[0],hD=S.hist[3]-S.hist[2];
const rgX0=S.ring[0]-cx,rgX1=S.ring[1]-cx,rgZ0=-(S.ring[3]-cy); // 繞歷建＝走開挖區（鋪鐵板）：西側 x0、東側 x1、北側 z0
const mX0=S.mound[0]-cx,mX1=S.mound[1]-cx,mZ0=-(S.mound[3]-cy),mZ1=-(S.mound[2]-cy),mXc=(mX0+mX1)/2,mZc=(mZ0+mZ1)/2;
const xF=S.x_f-cx,xU=S.x_u-cx,xLoad=S.x_load-cx;
const roadMat=0x5c5c5c;
function strip(x0,z0,x1,z1,w){const l=Math.hypot(x1-x0,z1-z0)+w*0.5,m=box(l,0.5,w,roadMat,(x0+x1)/2,0.25,(z0+z1)/2,false);m.rotation.y=Math.atan2(-(z1-z0),x1-x0);m.receiveShadow=true;}
{
 // 門外南北向馬路（雙向）
 strip(roadX,gZ-70,roadX,gZ+70,9);
 const mk2=(x0,z0,x1,z1)=>strip(x0,z0,x1,z1,6);
 strip(roadX,(zIn+zOut)/2,xW,(zIn+zOut)/2,8.5);     // 門內進出車道
 mk2(xW,zOut,xW,zE);mk2(xW,zE,xF,zE);mk2(xF,zE,xF,zW);mk2(xF,zW,xW,zW);
 // 鋪鐵板便道：繞歷建（開挖區）＋土堆南側車道穿過開挖區的路段
 const plate=(x0,z0,x1,z1)=>{const l=Math.hypot(x1-x0,z1-z0),m=box(l,0.62,6.4,0x8895a3,(x0+x1)/2,0.31,(z0+z1)/2,false);m.rotation.y=Math.atan2(-(z1-z0),x1-x0);m.receiveShadow=true;m.castShadow=false;
   const n=Math.floor(l/4);for(let k=1;k<n;k++){const q=k/n,sm=box(.12,.64,6.5,0x5d6672,x0+(x1-x0)*q,.32,z0+(z1-z0)*q,false);sm.rotation.y=m.rotation.y;}};
 plate(rgX0,zW,rgX0,rgZ0);plate(rgX0,rgZ0,rgX1,rgZ0);plate(rgX1,rgZ0,rgX1,zW);
 plate(rgX1,zW,xU,zW);plate(xU,zW,xU,zE);plate(rgX0,zE,rgX0,zW);plate(rgX1,zE,rgX1,zW);
 // 洗車台（出場車道往北通過，設在大門東側、洗車後轉西出門）
 const wsx=(S.wash[0]+S.wash[1])/2-cx,wsz=-((S.wash[2]+S.wash[3])/2-cy),wl=S.wash[1]-S.wash[0],ww=S.wash[3]-S.wash[2];
 box(wl,.7,ww,0x7fb6d8,wsx,.4,wsz);box(.4,1.4,ww,0x2c7fb8,wsx-wl/2+.2,1.1,wsz);box(.4,1.4,ww,0x2c7fb8,wsx+wl/2-.2,1.1,wsz);
 for(let a=0;a<4;a++)box(wl-1,.25,.25,0xdff3ff,wsx,.85,wsz-ww/2+1.2+a*(ww-2.4)/3);
 // 警衛室（大門北側）＋ 大門（南北向門線，兩根門柱＋橫桿）＋圍牆
 box(5,3,5,0xe74c3c,guX,1.5,guZ);box(5.6,.4,5.6,0x555555,guX,3.2,guZ);
 box(.8,4.4,.8,0x1f4fd8,gX,2.2,gzN);box(.8,4.4,.8,0x1f4fd8,gX,2.2,gzS);box(.5,.5,gzS-gzN,0x1f4fd8,gX,4.6,(gzN+gzS)/2);
 box(.3,2.4,70,0x8a8f99,gX,1.2,gzN-35);box(.3,2.4,70,0x8a8f99,gX,1.2,gzS+35);
 // 地磅站
 box(wW,.6,wD,0x9a9a9a,wc[0],.3,wc[1]);box(wW-1.2,.62,wD-1.2,0x3b3b3b,wc[0],.31,wc[1]);
 box(3,2.6,3,0xdddddd,wc[0]-5,1.3,wc[1]-wD/2-2.2);
 // 歷建：兩層樓保留建物 + 藍色圍籬
 var histG=new THREE.Group();scene.add(histG);
 const hb=(w,h,d,c,x,y,z)=>{const m=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),new THREE.MeshStandardMaterial({color:c,roughness:.8}));m.position.set(x,y,z);m.castShadow=true;m.receiveShadow=true;histG.add(m);};
 hb(hW,7.2,hD,0xc7926b,hcx,3.6,hcz);hb(hW+1.2,.5,hD+1.2,0x7a5a4a,hcx,7.45,hcz);hb(hW*.7,1.4,hD+1.0,0x6b4b3c,hcx,8.4,hcz);hb(hW*.3,1.2,hD+.6,0x6b4b3c,hcx,9.4,hcz);
 for(let r=0;r<4;r++)for(const sx of[-1,1]){hb(.15,1.6,1.4,0x3a5f7a,hcx+sx*(hW/2+.02),2.2,hcz-hD*.36+r*hD*.24);hb(.15,1.6,1.4,0x3a5f7a,hcx+sx*(hW/2+.02),5.4,hcz-hD*.36+r*hD*.24);}
 const f=S.fence,fw=hW+2*f,fd=hD+2*f;
 hb(fw,1.6,.15,0x1f4fd8,hcx,.8,hcz-fd/2);hb(fw,1.6,.15,0x1f4fd8,hcx,.8,hcz+fd/2);hb(.15,1.6,fd,0x1f4fd8,hcx-fw/2,.8,hcz);hb(.15,1.6,fd,0x1f4fd8,hcx+fw/2,.8,hcz);
 const lh=label('歷建（保留）');lh.position.set(hcx,12.5,hcz);histG.add(lh);
 for(const [t,x,z] of [['地磅站',wc[0],wc[1]+wD/2+3],['洗車台',wsx,wsz+ww/2+3],['大門',gX,gzS+4],['警衛室',guX,guZ-4]]){const l=label(t);l.position.set(x,5.5,z);scene.add(l);}
}
// 第一階段土堆（D'9‥D'14 × E'9‥E'14 圍起來，約 1.5 層樓高）
const moundG=new THREE.CylinderGeometry(0.62,1,1,4,1);moundG.rotateY(Math.PI/4);
const mound=new THREE.Mesh(moundG,new THREE.MeshStandardMaterial({color:0x7d5b3a,roughness:1,flatShading:true}));
mound.castShadow=true;mound.receiveShadow=true;mound.position.set(mXc,0,mZc);scene.add(mound);
const moundLabel=label('第一階段土堆');moundLabel.position.set(mXc,S.mound_h+4,mZc);scene.add(moundLabel);
// ---- 怪手 ----
const mk=c=>new THREE.MeshStandardMaterial({color:c,roughness:.6,metalness:.2});
const vs=Math.max(1,size/110);
function makeExc(){const ex=new THREE.Group(),exBody=new THREE.Group();ex.add(exBody);
 const tr=new THREE.Mesh(new THREE.BoxGeometry(3.4,.7,1.3),mk(0x222222));tr.position.y=.35;ex.add(tr);
 const bd=new THREE.Mesh(new THREE.BoxGeometry(2.2,1.1,1.5),mk(0xf1c40f));bd.position.y=1.25;exBody.add(bd);
 const cab=new THREE.Mesh(new THREE.BoxGeometry(.9,.9,1.2),mk(0x6fa8dc));cab.position.set(-.5,2.2,0);exBody.add(cab);
 const boom=new THREE.Group();boom.position.set(.9,1.8,0);exBody.add(boom);
 const b1=new THREE.Mesh(new THREE.BoxGeometry(2.8,.38,.4),mk(0xf1c40f));b1.position.x=1.4;boom.add(b1);
 const stick=new THREE.Group();stick.position.set(2.8,0,0);boom.add(stick);
 const b2=new THREE.Mesh(new THREE.BoxGeometry(.3,2.0,.3),mk(0xe0b000));b2.position.y=-1.0;stick.add(b2);
 const bucket=new THREE.Mesh(new THREE.BoxGeometry(.9,.5,.8),mk(0x444444));bucket.position.y=-2.1;stick.add(bucket);
 ex.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});ex.scale.setScalar(vs);scene.add(ex);
 return {ex,exBody,boom,stick};}
const E1=makeExc(),ex=E1.ex,exBody=E1.exBody,boom=E1.boom,stick=E1.stick;
const E2=makeExc();E2.ex.rotation.y=-Math.PI/2; // 站在土堆上往南裝車的怪手（第一階段）
// 便道斜坡（怪手所在位置 → 坑口）
const ramp=new THREE.Mesh(new THREE.BoxGeometry(1,.25,3.5),new THREE.MeshStandardMaterial({color:0x7a5a38,roughness:1}));ramp.castShadow=true;ramp.receiveShadow=true;ramp.visible=false;scene.add(ramp);
// ---- 土車 ----
const MAXT=14,POOL=24;
function truck(color){const g=new THREE.Group();
 const cab=new THREE.Mesh(new THREE.BoxGeometry(1.6,1.6,1.9),mk(0x2c3e50));cab.position.set(2.6,1.2,0);g.add(cab);
 const bed=new THREE.Mesh(new THREE.BoxGeometry(4.2,1.3,2.1),mk(color));bed.position.set(-.3,1.35,0);g.add(bed);
 const load=new THREE.Mesh(new THREE.BoxGeometry(3.8,.5,1.8),mk(0x6b4a2b));load.position.set(-.3,2.15,0);g.add(load);
 const ch=new THREE.Mesh(new THREE.BoxGeometry(6,.4,1.6),mk(0x222222));ch.position.y=.45;g.add(ch);
 g.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});g.scale.setScalar(vs*1.0);g.visible=false;scene.add(g);return {g,busy:false,load,hasLoad:true};}
const pools=[[],[]];for(let i=0;i<POOL;i++){pools[0].push(truck(0xe67e22));pools[1].push(truck(0x3498db));}
// ---- 時間軸 ----
let T=0,playing=true,speed=1,lastDay=-1;const DAYSEC=4.5,LIFE=1.6;
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
const RAMP_DIR=[0,1]; // 坑內斜坡一律往南（朝環場便道）
function depthAt(wx,wz){const i=Math.round((wx+cx-gx0)/step),j=Math.round((-wz+cy-gy0)/step);if(i<0||j<0||i>nx||j>ny)return 0;return -hts[j*(nx+1)+i];}
function floorY(){return -(exK>=0?curDep[exK]:0)*zex;}
function sched(i){ // 第 i 天的土車出發時間（天為單位）
  const d=days[i],n=Math.min(d.trips,MAXT),m=Math.min(d.fda,MAXT),out=[];
  for(let a=0;a<n;a++)out.push({t:i+(a+.5)/n*0.8,fda:0,id:a});
  for(let a=0;a<m;a++)out.push({t:i+(a+.5)/m*0.8+0.04,fda:1,id:MAXT+a});
  return out;}
const live=new Map();let seq=0;
const phase1=()=>slabDay===null||T<slabDay+0.5;
// 路線：門外馬路（北→南，左轉進場）→ 門內進場車道 → 西側便道往南 → 最南側車道往東
//  第一階段：（繞歷建走開挖區鐵板 或 直接）東行到最東側迴轉 → 轉到土堆南側車道往西，停在土堆旁（怪手站在土堆上裝車）
//  二挖以後：到挖掘坑（走斜坡）裝土 → 走復工版繞歷建一圈或到 F16 繞一圈 → 土堆南側車道
//  → 地磅站（停）→ 往北過洗車台（停）→ 出場車道往西 → 大門 → 右轉往北
function buildPath(variant,px,pz,fy,p1){
  const N=[],add=(x,z,y,st)=>{N.push({x,z,y:(y===undefined?0.55:y),stop:st||0});};
  const lx=xW-1.8,rx=xW+1.8,wsz2=-((S.wash[2]+S.wash[3])/2-cy);
  add(roadX-2,gZ-70);add(roadX-2,zIn-5);add(roadX+1.5,zIn);add(lx,zIn);add(lx,zE);
  const ringA=()=>{add(rgX0,zE);add(rgX0,rgZ0);add(rgX1,rgZ0);add(rgX1,zE);};
  if(p1){
    if(variant===0){ringA();add(xU,zE);add(xU,zW);}else{add(xF,zE);add(xF,zW);}
    add(xLoad,zW,0.55,0.08);
  }else{
    const L=Math.min(Math.max(8,Math.abs(fy)*3.2),Math.max(2,zE-pz-3));
    add(px,zE);add(px,pz+L,0.55);add(px,pz+2.0,fy,0.07);add(px,pz+L,0.55);add(px,zE);
    if(variant===0){add(rgX1,zE);add(rgX1,rgZ0);add(rgX0,rgZ0);add(rgX0,zW);}else{add(xF,zE);add(xF,zW);}
  }
  add(wc[0]+9,zW);add(wc[0],zW,0.55,0.1);add(rx,zW);add(rx,wsz2,0.55,0.06);add(rx,zOut);add(roadX+2,zOut);add(roadX+2,gZ-70);
  let tot=0,D=0;N[0].s=0;
  for(let i=1;i<N.length;i++){tot+=Math.hypot(N[i].x-N[i-1].x,N[i].z-N[i-1].z);N[i].s=tot;}
  const stops=[];for(const n of N)if(n.stop){stops.push({s:n.s,f:n.stop});D+=n.stop;}
  return {N,tot,stops,D};}
function ptAt(P,sd){const N=P.N;sd=Math.max(0,Math.min(P.tot,sd));let k=1;while(k<N.length-1&&N[k].s<sd)k++;
  const A=N[k-1],B=N[k],q=(sd-A.s)/((B.s-A.s)||1);return {x:A.x+(B.x-A.x)*q,z:A.z+(B.z-A.z)*q,y:A.y+(B.y-A.y)*q};}
function posOn(P,u){ // 時間軸 u∈[0,1]：移動佔 (1-D)，各停靠點（裝土、過磅）佔各自比例
  const mv=1-P.D;let t=0,s0=0,sd=null;
  for(const st of P.stops){const tm=(st.s-s0)/P.tot*mv;
    if(u<t+tm){sd=s0+(u-t)/tm*(st.s-s0);break;}t+=tm;
    if(u<t+st.f){sd=st.s;break;}t+=st.f;s0=st.s;}
  if(sd===null){const tm=(P.tot-s0)/P.tot*mv;sd=s0+Math.min(1,(u-t)/(tm||1))*(P.tot-s0);}
  const a=ptAt(P,sd),b1=ptAt(P,sd-3),b2=ptAt(P,sd+3);return {x:a.x,z:a.z,y:a.y,dx:b2.x-b1.x,dz:b2.z-b1.z,sd};}
function updateTrucks(){
  const di=Math.floor(T);
  for(const k of Array.from(live.keys())){const o=live.get(k);if(T-o.t>LIFE||T<o.t-0.01){o.tk.g.visible=false;o.tk.busy=false;live.delete(k);}}
  for(let i=Math.max(0,di-2);i<=Math.min(nd-1,di);i++)for(const s of sched(i)){
    const u=(T-s.t)/LIFE;if(u<0||u>1)continue;const key=i+'_'+s.id;
    if(!live.has(key)){const tk=pools[s.fda].find(x=>!x.busy);if(!tk)continue;tk.busy=true;
      live.set(key,{t:s.t,tk,P:buildPath((seq++)%2,exX,exZ,floorY(),phase1()),p1:phase1()});}
    const o=live.get(key),r=posOn(o.P,u);
    o.tk.g.visible=true;o.tk.g.position.set(r.x,r.y,r.z);o.tk.g.rotation.y=Math.atan2(-r.dz,r.dx);
    // 車斗：到裝土點之前是空的，裝完才有土；過了洗車台才算出場
    const sLoad=o.P.stops[0].s;o.tk.load.visible=r.sd>=sLoad-0.1;
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
  ex.rotation.y=Math.atan2(-RAMP_DIR[1],RAMP_DIR[0]);
  const dig=tg.length&&p<0.9,tt=performance.now()/1000;
  boom.rotation.z=dig?-0.35+0.28*Math.sin(tt*3.2):-0.2;stick.rotation.z=dig?0.5+0.3*Math.sin(tt*3.2+1.2):0.3;exBody.rotation.y=dig?0.4*Math.sin(tt*1.6):0;
  // 便道斜坡（坑底 → 坑口，往南）
  const L=Math.min(Math.max(8,Math.abs(fy)*3.2),Math.max(2,roadZ-exZ-3));
  if(Math.abs(fy)>0.3&&dig){const v=new THREE.Vector3(RAMP_DIR[0]*L,-fy,RAMP_DIR[1]*L),len=v.length();
    ramp.visible=true;ramp.scale.set(len,1,1);ramp.position.set(exX+v.x/2,fy+v.y/2+0.05,exZ+v.z/2);ramp.quaternion.setFromUnitVectors(new THREE.Vector3(1,0,0),v.normalize());}
  else ramp.visible=false;
  // 第一階段土堆與土堆旁的裝土怪手（二挖以後土堆清空）
  const mh=Math.min(1,T/1.5)*(slabDay===null?1:Math.max(0,Math.min(1,(slabDay+0.5-T)/Math.max(3,slabDay*0.4))));
  mound.scale.set(Math.max(0.01,mX1-mX0)/1.4142,Math.max(0.001,S.mound_h*mh),Math.max(0.01,mZ1-mZ0)/1.4142);mound.position.y=S.mound_h*mh/2;
  mound.visible=moundLabel.visible=mh>0.02;E2.ex.visible=mh>0.5;E2.ex.position.set(xLoad,S.mound_h*mh,mZc+(mZ1-mZc)*0.62-3.5);
  E2.boom.rotation.z=-0.3+0.25*Math.sin(tt*2.6);E2.stick.rotation.z=0.5+0.3*Math.sin(tt*2.6+1.0);E2.exBody.rotation.y=0.5*Math.sin(tt*1.3);
  labels.forEach((s,k)=>s.position.set(zs[k].wx,-curDep[k]*zex+size*0.012,zs[k].wz));
  const mode=document.getElementById('slabMode').value,want=(mode==='on'||(mode==='auto'&&slabDay!==null&&T>=slabDay+0.5))?1:0;
  const ds=want-slabCur;let slabChanged=false;if(Math.abs(ds)>0.001){slabCur+=Math.sign(ds)*Math.min(Math.abs(ds),0.04);slabChanged=true;}
  histG.visible=document.getElementById('ckHist').checked;
  if(di!==lastDay||p<0.9||slabChanged){updateTerrain();lastDay=di;}
  const d=days[di];
  const names=tg.map(s=>zs[s.k].c).join('、')||'（當天紀錄未指定分區）';
  info.innerHTML=`<b>${d.date}</b>　第 ${di+1}/${nd} 天<br>本日車次：${d.trips} 台${d.fda?`（另 FDA ${d.fda} 台）`:''}<br>本日方量：${d.vol.toLocaleString()} m³<br>累計方量：${d.cum.toLocaleString()} m³<br>挖掘分區：${names}<br>復工版：${slabCur>0.5?'已鋪設':(slabDay===null?'第一挖尚未完成':'第 '+(slabDay+1)+' 天後鋪設')}`;
  sl.value=di;
}
function resize(){const w=wrap.clientWidth,h=wrap.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}
window.addEventListener('resize',resize);resize();
playBtn.onclick=()=>{if(!playing&&T>=nd-0.001){T=0;lastDay=-1;live.forEach(o=>{o.tk.g.visible=false;o.tk.busy=false;});live.clear();}playing=!playing;playBtn.textContent=playing?'⏸ 暫停':'▶ 播放';};
document.getElementById('rst').onclick=()=>{T=0;lastDay=-1;live.forEach(o=>{o.tk.g.visible=false;o.tk.busy=false;});live.clear();playing=true;playBtn.textContent='⏸ 暫停';};
document.getElementById('spd').onchange=e=>speed=parseFloat(e.target.value);
sl.oninput=e=>{T=parseInt(e.target.value);lastDay=-1;live.forEach(o=>{o.tk.g.visible=false;o.tk.busy=false;});live.clear();};
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


def render_animation(zinfo, palette, days, title, z_exag, site, slab_day, height=720, base_url=THREE_CDN):
    data = json.dumps({"zones": zinfo, "palette": palette, "days": days, "zex": float(z_exag), "title": title,
                       "site": site, "slabDay": slab_day}, ensure_ascii=False)
    html = _HTML.replace("__BASE__", base_url).replace("__DATA__", data)
    components.html(html, height=height, scrolling=False)
    return html

"""3D 渲染實景（three.js / WebGL）：有光影、陰影、土層質感的工地挖掘畫面。

用 st.components.v1.html 內嵌，three.js 由使用者的瀏覽器從 CDN 載入（伺服器端不用裝任何東西）。
只有選到「3D 渲染實景」時才會產生，不影響其他畫面的速度。
"""
import json

import streamlit.components.v1 as components

THREE_VERSION = "0.160.0"
THREE_CDN = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}"

_HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
 html,body{margin:0;height:100%;overflow:hidden;background:#cfe3f5;font-family:'Segoe UI','Microsoft JhengHei',sans-serif}
 #wrap{position:relative;width:100%;height:100%}
 canvas{display:block}
 #title{position:absolute;left:12px;top:10px;background:rgba(255,255,255,.82);padding:6px 12px;border-radius:6px;font-weight:700;color:#1f3a5f;font-size:14px}
 #tip{position:absolute;pointer-events:none;background:rgba(20,30,45,.88);color:#fff;padding:6px 10px;border-radius:5px;font-size:12px;line-height:1.5;display:none;white-space:nowrap}
 #btn{position:absolute;right:12px;top:10px;background:#1f3a5f;color:#fff;border:0;border-radius:6px;padding:7px 12px;font-size:13px;cursor:pointer}
 #hint{position:absolute;left:12px;bottom:8px;background:rgba(255,255,255,.75);padding:4px 10px;border-radius:5px;font-size:12px;color:#333}
 #err{position:absolute;left:20px;top:60px;color:#b00;font-size:14px}
</style>
<script type="importmap">{"imports":{"three":"__BASE__/build/three.module.js","three/addons/":"__BASE__/examples/jsm/"}}</script>
</head><body><div id="wrap"><div id="title"></div><button id="btn">📷 存成圖片</button><div id="tip"></div>
<div id="hint">左鍵旋轉　右鍵平移　滾輪縮放　滑鼠移到坑上看資訊</div><div id="err"></div></div>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const D = __DATA__;
const wrap = document.getElementById('wrap');
document.getElementById('title').textContent = D.title;
try {
const zs = D.zones, zex = D.zex;
let X0=1e18,X1=-1e18,Y0=1e18,Y1=-1e18;
zs.forEach(z=>{X0=Math.min(X0,z.x0);X1=Math.max(X1,z.x1);Y0=Math.min(Y0,z.y0);Y1=Math.max(Y1,z.y1);});
const W=X1-X0,H=Y1-Y0,size=Math.max(W,H),cx=(X0+X1)/2,cy=(Y0+Y1)/2;
const step=size/280, pad=size*0.12;
const gx0=X0-pad, gy0=Y0-pad, nx=Math.ceil((W+2*pad)/step), ny=Math.ceil((H+2*pad)/step);
// ---- 分區格網索引 ----
const zi=new Int16Array((nx+1)*(ny+1)).fill(-1);
zs.forEach((z,k)=>{
  const i0=Math.max(0,Math.ceil((z.x0-gx0)/step)), i1=Math.min(nx,Math.ceil((z.x1-gx0)/step)-1);
  const j0=Math.max(0,Math.ceil((z.y0-gy0)/step)), j1=Math.min(ny,Math.ceil((z.y1-gy0)/step)-1);
  for(let j=j0;j<=j1;j++)for(let i=i0;i<=i1;i++)zi[j*(nx+1)+i]=k;
});
const hash=(a,b)=>{const s=Math.sin(a*127.1+b*311.7)*43758.5453;return s-Math.floor(s);};
const vnoise=(x,y)=>{const xi=Math.floor(x),yi=Math.floor(y),xf=x-xi,yf=y-yi;
  const u=xf*xf*(3-2*xf),v=yf*yf*(3-2*yf);
  return (hash(xi,yi)*(1-u)+hash(xi+1,yi)*u)*(1-v)+(hash(xi,yi+1)*(1-u)+hash(xi+1,yi+1)*u)*v;};
const fbm=(x,y)=>0.5*vnoise(x,y)+0.3*vnoise(x*2.3,y*2.3)+0.2*vnoise(x*5.1,y*5.1);
// ---- 地形高度 ----
const NV=(nx+1)*(ny+1);
const hts=new Float32Array(NV);
for(let n=0;n<NV;n++){const k=zi[n];hts[n]=k>=0?-zs[k].d*zex:0;}
// ---- 幾何（x→世界x，北方(Y大)→世界-z）----
const pos=new Float32Array(NV*3),col=new Float32Array(NV*3);
const strata=d=>{ // 依深度的土層顏色
  if(d<1.0)return [0.33,0.22,0.12];      // 表土
  if(d<3.0)return [0.62,0.46,0.28];      // 黏土/粉土
  if(d<6.0)return [0.55,0.5,0.42];       // 砂質
  return [0.45,0.45,0.46];               // 礫石/岩盤
};
const ground=[0.62,0.55,0.4];
const c3=s=>{const m=s.match(/[\d.]+/g);return [m[0]/255,m[1]/255,m[2]/255];};
const zcol=zs.map(z=>c3(z.col));
for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++){
  const n=j*(nx+1)+i, X=gx0+i*step, Y=gy0+j*step;
  pos[n*3]=X-cx; pos[n*3+1]=hts[n]; pos[n*3+2]=-(Y-cy);
  let hmax=0;
  for(const [di,dj] of [[1,0],[-1,0],[0,1],[0,-1]]){
    const ii=i+di,jj=j+dj; if(ii<0||jj<0||ii>nx||jj>ny)continue;
    hmax=Math.max(hmax,Math.abs(hts[jj*(nx+1)+ii]-hts[n]));
  }
  const nz=0.82+0.36*fbm(X*0.9,Y*0.9), k=zi[n];
  let c;
  if(hmax>step*0.4){ c=strata(-hts[n]/zex); }                  // 坑壁：土層
  else if(k>=0&&zs[k].d>0){ c=zcol[k]; }                        // 坑底：狀態顏色
  else c=ground;
  col[n*3]=Math.min(1,c[0]*nz);col[n*3+1]=Math.min(1,c[1]*nz);col[n*3+2]=Math.min(1,c[2]*nz);
}
const idx=[];
for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){const a=j*(nx+1)+i,b=a+1,c=a+nx+1,d=c+1;idx.push(a,b,c,b,d,c);}
const geo=new THREE.BufferGeometry();
geo.setAttribute('position',new THREE.BufferAttribute(pos,3));
geo.setAttribute('color',new THREE.BufferAttribute(col,3));
geo.setIndex(idx);geo.computeVertexNormals();
// ---- 場景 ----
const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(2,window.devicePixelRatio||1));
renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=0.82;
wrap.appendChild(renderer.domElement);
const scene=new THREE.Scene();
const sky=new THREE.Color(0xbfd9f0);scene.background=sky;scene.fog=new THREE.Fog(sky,size*2.2,size*6);
const camera=new THREE.PerspectiveCamera(42,1,size*0.01,size*12);
camera.position.set(size*0.45,size*0.62,size*0.78);
const controls=new OrbitControls(camera,renderer.domElement);
controls.target.set(0,-1,0);controls.enableDamping=true;controls.maxPolarAngle=Math.PI/2-0.03;
scene.add(new THREE.HemisphereLight(0xdfeeff,0x8a7a5a,0.7));
const sun=new THREE.DirectionalLight(0xfff3dc,2.0);
sun.position.set(size*0.7,size*0.9,size*0.35);sun.castShadow=true;
sun.shadow.mapSize.set(4096,4096);const sc=sun.shadow.camera,r=size*0.85;
sc.left=-r;sc.right=r;sc.top=r;sc.bottom=-r;sc.near=1;sc.far=size*4;sun.shadow.bias=-0.0004;sun.shadow.normalBias=0.05;
scene.add(sun);
const mat=new THREE.MeshStandardMaterial({vertexColors:true,roughness:0.96,metalness:0});
const terrain=new THREE.Mesh(geo,mat);terrain.castShadow=true;terrain.receiveShadow=true;scene.add(terrain);
// 周邊大地面（中間挖空，不能蓋住坑）
const hx0=gx0-cx+step*0.6,hx1=gx0+nx*step-cx-step*0.6,hy0=-(gy0+ny*step-cy)+step*0.6,hy1=-(gy0-cy)-step*0.6,R=size*7;
const shp=new THREE.Shape();shp.moveTo(-R,-R);shp.lineTo(R,-R);shp.lineTo(R,R);shp.lineTo(-R,R);shp.lineTo(-R,-R);
const hole=new THREE.Path();hole.moveTo(hx0,hy0);hole.lineTo(hx0,hy1);hole.lineTo(hx1,hy1);hole.lineTo(hx1,hy0);hole.lineTo(hx0,hy0);shp.holes.push(hole);
const far=new THREE.Mesh(new THREE.ShapeGeometry(shp),new THREE.MeshStandardMaterial({color:0x8f8466,roughness:1}));
far.rotation.x=-Math.PI/2;far.position.y=-0.01;far.receiveShadow=true;scene.add(far);
// 分區代號標籤
function label(text){
  const cv=document.createElement('canvas');cv.width=128;cv.height=48;const g=cv.getContext('2d');
  g.font='bold 30px sans-serif';g.textAlign='center';g.textBaseline='middle';
  g.lineWidth=6;g.strokeStyle='rgba(255,255,255,.9)';g.strokeText(text,64,24);g.fillStyle='#1b1b1b';g.fillText(text,64,24);
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(cv),transparent:true}));
  sp.scale.set(size*0.06,size*0.0225,1);return sp;
}
zs.forEach(z=>{const s=label(z.c);s.position.set((z.x0+z.x1)/2-cx,-z.d*zex+size*0.012,-((z.y0+z.y1)/2-cy));scene.add(s);});
// 怪手（點綴）：放在最深的坑
let deep=null;zs.forEach(z=>{if(z.d>0&&(!deep||z.d>deep.d))deep=z;});
if(deep){
  const g=new THREE.Group(),m=(c)=>new THREE.MeshStandardMaterial({color:c,roughness:.6,metalness:.2});
  const tr=new THREE.Mesh(new THREE.BoxGeometry(3.4,0.7,1.2),m(0x222222));tr.position.y=0.35;g.add(tr);
  const bd=new THREE.Mesh(new THREE.BoxGeometry(2.2,1.1,1.5),m(0xf1c40f));bd.position.set(0,1.25,0);g.add(bd);
  const cab=new THREE.Mesh(new THREE.BoxGeometry(0.9,0.9,1.2),m(0x6fa8dc));cab.position.set(-0.5,2.2,0);g.add(cab);
  const b1=new THREE.Mesh(new THREE.BoxGeometry(2.6,0.35,0.4),m(0xf1c40f));b1.position.set(2.0,2.2,0);b1.rotation.z=0.55;g.add(b1);
  const b2=new THREE.Mesh(new THREE.BoxGeometry(2.0,0.28,0.32),m(0xe0b000));b2.position.set(3.7,2.2,0);b2.rotation.z=-0.7;g.add(b2);
  g.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});
  const sc2=Math.max(1,size/70);g.scale.setScalar(sc2);
  g.position.set((deep.x0+deep.x1)/2-cx,-deep.d*zex,-((deep.y0+deep.y1)/2-cy));g.rotation.y=0.6;scene.add(g);
}
// 滑鼠提示
const tip=document.getElementById('tip'),ray=new THREE.Raycaster(),mp=new THREE.Vector2();
renderer.domElement.addEventListener('mousemove',e=>{
  const rc=renderer.domElement.getBoundingClientRect();
  mp.x=((e.clientX-rc.left)/rc.width)*2-1;mp.y=-((e.clientY-rc.top)/rc.height)*2+1;
  ray.setFromCamera(mp,camera);const h=ray.intersectObject(terrain)[0];
  if(!h){tip.style.display='none';return;}
  const X=h.point.x+cx,Y=-h.point.z+cy,i=Math.round((X-gx0)/step),j=Math.round((Y-gy0)/step);
  const k=(i>=0&&j>=0&&i<=nx&&j<=ny)?zi[j*(nx+1)+i]:-1;
  if(k<0){tip.style.display='none';return;}
  tip.innerHTML=zs[k].t;tip.style.display='block';
  tip.style.left=Math.min(e.clientX-rc.left+14,rc.width-220)+'px';tip.style.top=(e.clientY-rc.top+14)+'px';
});
renderer.domElement.addEventListener('mouseleave',()=>tip.style.display='none');
document.getElementById('btn').onclick=()=>{renderer.render(scene,camera);const a=document.createElement('a');a.download='3D渲染實景.png';a.href=renderer.domElement.toDataURL('image/png');a.click();};
function resize(){const w=wrap.clientWidth,h=wrap.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}
window.addEventListener('resize',resize);resize();
(function loop(){controls.update();renderer.render(scene,camera);requestAnimationFrame(loop);})();
window.__ready=true;
} catch(e){document.getElementById('err').textContent='3D 渲染失敗：'+e;}
</script></body></html>"""


def render_realistic(zones, depth_m, color_of, hover_of, title, z_exag, height=680, base_url=THREE_CDN):
    """zones：df（含分區代號與 x_min/x_max/y_min/y_max）；其餘參數同 map_3d.render_pit_3d。"""
    items = []
    for _, r in zones.iterrows():
        c = r["分區代號"]
        items.append({
            "c": str(c), "x0": float(r["x_min"]), "x1": float(r["x_max"]), "y0": float(r["y_min"]), "y1": float(r["y_max"]),
            "d": float(depth_m.get(c, 0) or 0), "col": color_of.get(c, "rgb(196,170,128)"), "t": hover_of.get(c, str(c)),
        })
    data = json.dumps({"zones": items, "zex": float(z_exag), "title": title}, ensure_ascii=False)
    html = _HTML.replace("__BASE__", base_url).replace("__DATA__", data)
    components.html(html, height=height, scrolling=False)
    return html

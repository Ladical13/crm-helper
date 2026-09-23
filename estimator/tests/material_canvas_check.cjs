// Optional native-canvas integration check: NODE_PATH must include @napi-rs/canvas.
// Uses the application's actual compositor, not a separate demonstration shader.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const {createCanvas}=require('@napi-rs/canvas');
const src=fs.readFileSync(require('path').join(__dirname,'../static/app.js'),'utf8');
const from=src.indexOf('const _SIDING_PATTERN_SVG =');
const to=src.indexOf('// ── Service Worker registration',from);
const width=480,height=300;
const photo=createCanvas(width,height),pc=photo.getContext('2d');
pc.fillStyle='#c5e0ed';pc.fillRect(0,0,width,height);
pc.fillStyle='#afaa9b';pc.fillRect(90,150,300,140);
pc.fillStyle='#323632';pc.fillRect(300,220,45,70);
const gradient=pc.createLinearGradient(0,70,0,180);
gradient.addColorStop(0,'#b0b0b0');gradient.addColorStop(1,'#505050');
pc.fillStyle=gradient;pc.beginPath();pc.moveTo(85,65);pc.lineTo(350,65);pc.lineTo(420,185);pc.lineTo(35,185);pc.closePath();pc.fill();
pc.save();pc.clip();pc.strokeStyle='#343434';pc.lineWidth=2;
for(let x=50;x<420;x+=28){pc.beginPath();pc.moveTo(x,65);pc.lineTo(x-20,185);pc.stroke();}
pc.restore();
pc.fillStyle='#724c30';pc.fillRect(35,178,385,12); // fascia under an intentionally overlapping roof mask
photo.complete=true;photo.naturalWidth=width;photo.naturalHeight=height;
const roof=createCanvas(width,height),rc=roof.getContext('2d');
rc.fillStyle='#fff';rc.beginPath();rc.moveTo(85,65);rc.lineTo(350,65);rc.lineTo(420,190);rc.lineTo(35,190);rc.closePath();rc.fill();
const trim=createCanvas(width,height);trim.getContext('2d').fillStyle='#fff';trim.getContext('2d').fillRect(35,178,385,12);
const ctx=vm.createContext({console,document:{getElementById:()=>null,createElement:()=>createCanvas(1,1)},
  S:{estimate_id:'test',trades:{}},priceBook:{exterior_catalog:[],exterior_doors:[]},
  TIERS:['good','better','best'],Image:function(){throw Error('Prepared cache should be reused');},
  fetch(){throw Error('Rendering colors must not perform network calls');},
  setDirty(){},photo,roof,trim,width,height,createCanvas,assert});
vm.runInContext(src.slice(from,to),ctx);
vm.runInContext(`
_vzResetState();
S.visualizer={scope:['roof'],selections:{roofing:{
  good:{product_name:'Standing Seam Metal',color_hex:'#484e58'},
  better:{product_name:'Standing Seam Metal',color_hex:'#687963'},
  best:{product_name:'Standing Seam Metal',color_hex:'#bdaf99'}
}},elevations:{front:{id:'front',base_image:'test/original.png',masks:{},tier_renders:{}}}};
const ev=_vzElevation();
ev.material_layers=[{version:1,role:'roof',identity:_vzMaterialIdentity(S.visualizer.selections.roofing.good),
  base_image:ev.base_image,image_ref:'test/prepared.png',reference_luma:.17}];
_vzMaterialImages.set('test/prepared.png',photo);
vzState.photoImg=photo;vzState.roofMask=roof;vzState.trimMask=trim;
const original=photo.getContext('2d').getImageData(0,0,width,height).data;
globalThis.results=[];
for(const tier of TIERS) {
  const output=createCanvas(width,height);_vzComposeInto(output,tier,{});
  const pixels=output.getContext('2d').getImageData(0,0,width,height).data;
  for(const [x,y] of [[10,10],[250,240],[100,183],[300,183]]){
    const i=(y*width+x)*4;
    assert.deepEqual([...pixels.slice(i,i+4)],[...original.slice(i,i+4)],'Background and fascia must remain pixel-identical');
  }
  const a=(120*width+200)*4,b=(120*width+209)*4;
  assert.notDeepEqual([...pixels.slice(a,a+3)],[...original.slice(a,a+3)],'Selected roof must change');
  assert.notDeepEqual([...pixels.slice(a,a+3)],[...pixels.slice(b,b+3)],'Seam detail must remain');
  results.push(output);
}
assert.equal(_vzMaterialColors.size,3);
const again=createCanvas(width,height);_vzComposeInto(again,'good',{});
assert.equal(_vzMaterialColors.size,3,'Repeated comparisons reuse cached color layers');
`,ctx);
if(process.argv[2]){
  const board=createCanvas(width*3,height+40),bc=board.getContext('2d');
  bc.fillStyle='#fff';bc.fillRect(0,0,board.width,board.height);
  ctx.results.forEach((canvas,i)=>{bc.drawImage(canvas,i*width,40);bc.fillStyle='#182e36';bc.font='18px sans-serif';bc.fillText(['Charcoal','Sage','Warm beige'][i]+' · same material layer',i*width+15,26);});
  fs.writeFileSync(process.argv[2],board.toBuffer('image/png'));
}
console.log('Native canvas checks passed: three colors, preserved seams, exact protected fascia/background pixels, cached repeats, zero provider calls.');

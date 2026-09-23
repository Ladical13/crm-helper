"""Rendering, retry, and surface-removal regressions against the real JS."""
from test_visualizer import _run_visualizer_ui_node


def test_flat_material_scale_is_relative_to_photo_not_output_pixels():
    _run_visualizer_ui_node(r"""
_vzResetState(); vzState.canvas={width:1400,height:700};
const tiles=[];
_vzMakeMaskCanvas=(w,h)=>({width:w,height:h,getContext:()=>({drawImage(){}})});
const ctx={createPattern(tile){tiles.push(tile);return {};},fillRect(){}};
const swatch={naturalWidth:512,naturalHeight:256};
for (const w of [1400,2048,280]) {
  assert.equal(_vzFillCanonicalFlat(ctx,w,w/2,swatch,100,'square'),true);
  const tile=tiles.at(-1);
  assert.ok(Math.abs(tile.width/w-100/1400)<1/w);
  assert.ok(Math.abs(tile.height/w-50/1400)<1/w);
}
_vzFillCanonicalFlat(ctx,280,140,{naturalWidth:80,naturalHeight:40},100,'native');
assert.equal(tiles.at(-1).width,16);
assert.equal(tiles.at(-1).height,8);
// Both fallback compositors must use this same coordinate system.
const calls=[];
_vzFillCanonicalFlat=(...args)=>{calls.push(args);return true;};
const layer={drawImage(){}};
document.createElement=()=>({getContext:()=>layer});
const output={save(){},restore(){},drawImage(){}};
_vzCompositeTexture(output,2048,1024,{},swatch,100);
_vzCompositePattern(output,280,140,{},swatch);
assert.deepEqual(calls.map(args=>[args[1],args[2],args[5]]),
  [[2048,1024,'square'],[280,140,'native']]);
""")


def test_trim_finish_uses_original_lighting_and_overrides_lower_surfaces():
    _run_visualizer_ui_node(r"""
_vzResetState(); const original={id:'original'}, mask={id:'trim'};
vzState.photoImg=original;
const draws=[], fills=[];
const layer={globalAlpha:1,globalCompositeOperation:'source-over',
  fillRect(){fills.push(this.fillStyle);},
  drawImage(image){draws.push([image,this.globalCompositeOperation,this.globalAlpha]);}};
const canvas={getContext:()=>layer}; document.createElement=()=>canvas;
const output={save(){},restore(){},drawImage(image){
  assert.equal(image,canvas);assert.equal(this.globalCompositeOperation,'source-over');}};
_vzCompositeColor(output,20,10,mask,'#ffffff');
assert.deepEqual(fills,['#ffffff']);
assert.deepEqual(draws,[[original,'luminosity',0.24],[mask,'destination-in',1]]);
""")


def test_photo_only_save_retries_after_images_uploaded_but_metadata_failed():
    _run_visualizer_ui_node(r"""
_vzResetState(); vzState.photoImg={naturalWidth:1200,naturalHeight:800};
vzState.pendingBaseDataUrl='data:image/jpeg;base64,AAAA'; vzState.dirty=true;
_vzDetectionUI=()=>{}; _vzMaskHasContent=()=>false;
const assets=[]; let attempts=0;
_vzPostAsset=async(eid,body)=>{assets.push(body);return {filename:eid+'/base.jpg'};};
fetch=async()=>({ok:++attempts>1,json:async()=>({error:'offline'})});
assert.equal(await _vzSaveAll(),false);
assert.equal(vzState.pendingBaseDataUrl,null);
assert.equal(vzState.dirty,true);
assert.equal(_vzMetaPending(S),true);
assert.equal(await _vzSaveAll(),true);
assert.equal(vzState.dirty,false);
assert.equal(_vzMetaPending(S),false);
assert.deepEqual(assets.map(asset=>asset.kind),['base']);
""")


def test_clearing_last_surface_saves_blank_masks_and_removes_old_renders():
    _run_visualizer_ui_node(r"""
S.visualizer={scope:['trim'],base_image:'estimate-a/photo.jpg',
  trim_mask:'estimate-a/mask.png',tier_renders:{good:'estimate-a/old.jpg'}};
_vzResetState(); vzState.photoImg={naturalWidth:1200,naturalHeight:800};
vzState.trimMask={toDataURL:()=> 'data:image/png;base64,AAAA'};
vzState.dirty=true; _vzDetectionUI=()=>{};_vzMaskHasContent=()=>false;
const assets=[], metadata=[];
_vzPostAsset=async(eid,body)=>{assets.push(body);return {filename:eid+'/blank.png'};};
fetch=async(url,options)=>{metadata.push(JSON.parse(options.body));return {ok:true};};
assert.equal(await _vzSaveAll(),true);
assert.deepEqual(assets.map(asset=>[asset.kind,asset.role]),[['mask','trim']]);
assert.equal(metadata[0].invalidate_current_renders,true);
assert.deepEqual(_vzElevation().tier_renders,{});
assert.deepEqual(S.visualizer.tier_renders,{});
""")


def test_deleting_front_does_not_recreate_it_from_client_legacy_mirrors():
    _run_visualizer_ui_node(r"""
S.visualizer={base_image:'estimate-a/front.jpg',
  tier_renders:{good:'estimate-a/old.jpg'},trim_mask:'estimate-a/mask.png',
  elevations:{rear:{id:'rear',name:'Rear',base_image:'estimate-a/rear.jpg'}},
  active_elevation_id:'front'};
_vzResetState(); _vzGet();
renderVisualizerPage=async()=>{};
const metadata=[];
fetch=async(url,options)=>{metadata.push(JSON.parse(options.body));return {ok:true};};
await _vzDeleteElevation();
assert.equal(_vzGet().elevations.front,undefined);
assert.deepEqual(_vzGet().elevation_order,['rear']);
assert.equal(metadata[0].delete_elevation_id,'front');
assert.equal(metadata[0].elevation_names.front,undefined);
""")


def test_required_images_fail_explicitly_instead_of_saving_incomplete_design():
    _run_visualizer_ui_node(r"""
await assert.rejects(_vzImageReady(null),/unavailable/);
await assert.rejects(_vzImageReady({complete:true,naturalWidth:0}),/could not be loaded/);
const loaded={complete:true,naturalWidth:10};
assert.equal(await _vzImageReady(loaded),loaded);
let timeout;
setTimeout=fn=>{timeout=fn;return 1;};
const events=new Map();
const waiting=_vzImageReady({complete:false,
  addEventListener:(event,handler)=>events.set(event,handler),
  removeEventListener:event=>events.delete(event)});
timeout();
await assert.rejects(waiting,/timed out/);
assert.equal(events.size,0);
""")

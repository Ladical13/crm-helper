// Real renderer math and cache identity, evaluated without any provider/network.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(require('path').join(__dirname,'../static/app.js'),'utf8');
const begin=source.indexOf('// Reusable material layers:');
const end=source.indexOf('// Realistic edits are separate',begin);
assert(begin>0 && end>begin);
const row={product_name:'Standing Seam Metal',style_id:'16-inch',color_hex:'#808080'};
const ev={base_image:'test/original.png',material_layers:[]};
const owner={estimate_id:'test',visualizer:{selections:{roofing:{good:row,best:{...row,color_hex:'#eeeeee'}}}}};
const ctx={Map,Array,Number,Math,JSON,Object,String,parseInt,Uint8ClampedArray,
  S:owner,vzState:{pendingBaseDataUrl:null},_vzElevation:()=>ev,_vzGet:()=>owner.visualizer,
  _VZ_ROLE_META:{roof:{trade:'roofing'},siding:{trade:'siding'}},
  fetch(){throw new Error('Color comparison must not call a provider');}
};
vm.createContext(ctx);vm.runInContext(source.slice(begin,end),ctx);
const reference=((128/255+.055)/1.055)**2.4;
const sample=new Uint8ClampedArray([50,50,50,255,128,128,128,255,210,210,210,128]);
assert(ctx._vzRecolorMaterialPixels(sample,'#a04030',reference));
assert.deepEqual([...sample.slice(4,7)],[160,64,48],'Reference lighting must map to the selected hex');
assert(sample[0]<sample[4] && sample[4]<sample[8],'Shadows and seam highlights must survive');
assert.equal(sample[11],128,'Alpha must remain intact');
const light=new Uint8ClampedArray([128,128,128,255]);
ctx._vzRecolorMaterialPixels(light,'#f0f0f0',reference);
assert.deepEqual([...light.slice(0,3)],[240,240,240],'Light finishes must be able to lighten the original');
assert.equal(ctx._vzRecolorMaterialPixels(light,'invalid',reference),false);
assert.equal(ctx._vzRecolorMaterialPixels(light,'#ffffff',NaN),false);
ev.material_layers=[{version:1,role:'roof',identity:ctx._vzMaterialIdentity(row),base_image:ev.base_image}];
assert(ctx._vzMaterialFor('roof','good'));
assert(ctx._vzMaterialFor('roof','best'),'Same style must be reusable across concepts and colors');
row.color_hex='#c00000';row.color_name='Red';row.texture_ref='_catalog/et_color.png';
assert(ctx._vzMaterialFor('roof','good'),'Color and swatch changes must not invalidate style');
row.style_id='12-inch';assert.equal(ctx._vzMaterialFor('roof','good'),null);
row.style_id='16-inch';ev.base_image='test/new.png';assert.equal(ctx._vzMaterialFor('roof','good'),null);
ev.base_image='test/original.png';ctx.vzState.pendingBaseDataUrl='data:new-photo';
assert.equal(ctx._vzMaterialFor('roof','good'),null);
assert(!ctx._vzMaterialEligible('roof',{product_name:'IKO Nordic',color_hex:'#777777'}));
assert(ctx._vzMaterialEligible('siding',{product_name:'LP SmartSide',color_hex:'#777777'}));
assert(!ctx._vzMaterialEligible('siding',{product_name:'Natural wood',color_hex:'#777777'}));
console.log('Material recolor, lighting, alpha, style identity and no-network checks passed.');

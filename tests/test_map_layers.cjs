const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const groups=[],elements=new Map();let collapsed=false;
const el=()=>({textContent:'',style:{},offsetWidth:1000,addEventListener(){}});
const document={body:{classList:{contains(){return collapsed}}},getElementById(id){if(!elements.has(id))elements.set(id,el());return elements.get(id)}};
const map={setView(){return this},getContainer(){return el()},getSize(){return{x:1000,y:800}},getZoom(){return 15},getCenter(){return{lat:40.75,lng:-73.98}},on(){},invalidateSize(){},fitBounds(bounds,opts){this.bounds=bounds;this.padding=opts;return this},attributionControl:{addAttribution(){},removeAttribution(){}}};
function group(){const g={items:[],clears:0,addTo(){return this},clearLayers(){this.clears++;this.items=[]}};groups.push(g);return g}
function shape(_position,options){return{options,addTo(g){if(g.items)g.items.push(this);return this},bindPopup(){return this},on(name,fn){this[name]=fn;return this}}}
const L={map(){return map},control:{zoom(){return shape()}},tileLayer(){return shape()},layerGroup:group,featureGroup:group,marker:shape,polyline:shape,divIcon(x){return x},latLngBounds(x){return x}};
const context=vm.createContext({L,document,ResizeObserver:class{observe(){}},setTimeout(){},clearTimeout(){},console});
vm.runInContext(fs.readFileSync('static/map.js','utf8'),context);
const p={id:'p',name:'Place',lat:40.75,lon:-73.98};
const j={planned_at:'fixture',origin:p,destination:p,duration:20,steps:[{kind:'walk',leg:0}],legs:[{transit:true,line:'Q',color:'FCCC0A',from:'Times Sq',to:'Canal',geometry:[[40.75,-73.98],[40.72,-74]],boarding:p,alighting:p}]};
const s={origin:p,destination:{...p,id:'end'},journeys:[j],selected:0,step:0,nearby_stations:[{...p,id:'old',distance_m:100}]};
const api=context.subwayMap,routes=groups[1],markers=groups[2];let count=0;
function test(name,fn){fn();console.log('PASS '+name);count++}
test('expiry alone clears stale route geometry and nearby station markers',()=>{api.update(s);assert.equal(routes.items.length,2);const clears=routes.clears;api.update({...s,journey_expired:true});assert.equal(routes.clears,clears+1);assert.equal(routes.items.length,0);assert.equal(markers.items.length,2);assert.match(elements.get('map-caption').textContent,/Earlier journey paused/);assert.doesNotMatch(elements.get('map-caption').textContent,/chat/)});
test('new search does not fall back to a previously selected place',()=>{const d={active:true,stage:'points',points:[p]};api.preview(d,[],{searchEdited:false});assert.equal(markers.items.length,1);api.preview(d,[],{searchEdited:true});assert.equal(markers.items.length,0)});
test('confirmed location focuses its coordinates without renumbering',()=>{const selected={...p,id:'broadway',lat:40.754,lon:-73.986};api.preview({active:true,stage:'search',role:'destination',origin:selected},[],{searchEdited:false});assert.equal(markers.items.length,1);assert.match(markers.items[0].options.icon.html,/>A</);assert.equal(map.bounds[0][0],selected.lat);assert.equal(map.bounds[0][1],selected.lon);assert.equal(map.padding.animate,false)});
test('editing the origin focuses A even when B already exists',()=>{const origin={...p,id:'edited-origin',lat:40.754,lon:-73.986};api.preview({active:true,stage:'review',role:'destination',origin,destination:{...p,id:'dest'},last_selected_role:'origin'},[],{searchEdited:false});assert.equal(map.bounds[0][0],origin.lat);assert.equal(map.bounds[0][1],origin.lon)});
test('search marker chooses the matching result',()=>{let selected=null;api.preview({active:true,stage:'search'},[p],{place(id){selected=id},point(){throw Error('Wrong action')}});markers.items[0].click();assert.equal(selected,'p')});
test('hiding directions releases map space for the route',()=>{api.update(s);collapsed=false;api.focusStep();assert.equal(map.padding.paddingBottomRight[0],390);collapsed=true;api.focusStep();assert.equal(map.padding.paddingBottomRight[0],45)});
test('boarding marker uses the actual train color and contrasting text',()=>{const red={...j,planned_at:'red-line',legs:[{...j.legs[0],line:'2',color:'EE352E'}]};api.update({...s,journeys:[red]});const marker=markers.items.find(m=>m.options.icon.className.includes('boarding'));assert.match(marker.options.icon.html,/background:#EE352E/);assert.match(marker.options.icon.html,/color:#fff/)});
console.log(count+' map-layer checks passed');

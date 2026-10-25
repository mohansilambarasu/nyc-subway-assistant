/* Verify the map picker UI contract using the shipped renderer. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let click;const root={innerHTML:'',children:[1],querySelector(){return null},querySelectorAll(){return[]},addEventListener(_,fn){click=fn}};
const card={innerHTML:'',querySelectorAll(){return[]}};
const document={body:{classList:{toggle(){}}},getElementById(id){return id==='map-planner'?root:id==='map-journey'?card:null}};
const ctx=vm.createContext({document,console,setTimeout,clearTimeout,AbortController});vm.runInContext(fs.readFileSync('static/map-planner.js','utf8'),ctx);
let action;const options={cardHTML:'',quickHTML:'',busy:false,onAction(p){action=p}};let n=0;
function check(name,fn){fn();n++;console.log('PASS '+name)}
const a={id:'a',name:"Joe's Pizza",address:'1435 Broadway'};
const b={id:'b',name:'Charging Bull',address:'Broadway'};
function render(d){ctx.mapPlanner.update({map_draft:d},options);return root.innerHTML}
check('search precedes endpoint selectors and empty endpoints do not offer edit pencils',()=>{const html=render({active:true,role:'origin',stage:'search'});assert.ok(html.indexOf('id="location-search"')<html.indexOf('class="endpoint-list"'));assert.doesNotMatch(html,/endpoint-edit/);assert.match(html,/Hide search/);assert.doesNotMatch(html,/Show map|data-planner="submit"/);assert.match(html,/aria-pressed="true"/)});
check('confirmed A can be changed while destination is the active selector',()=>{const html=render({active:true,role:'destination',stage:'search',origin:a});assert.match(html,/Change starting point: Joe&#39;s Pizza/);assert.equal((html.match(/endpoint-edit/g)||[]).length,1);assert.match(html,/Search destination/)});
check('review offers one route action and both confirmed addresses',()=>{const html=render({active:true,role:'destination',stage:'review',origin:a,destination:b});assert.match(html,/Find routes/);assert.match(html,/1435 Broadway/);assert.match(html,/Change destination: Charging Bull/);assert.doesNotMatch(html,/id="location-search"/);assert.equal((html.match(/data-planner="submit"/g)||[]).length,1)});
check('collapse is labelled Show search and retains selected locations',()=>{click({target:{closest(){return {hasAttribute(n){return n==='data-toggle-panel'},dataset:{}}}}});assert.match(root.innerHTML,/Show search/);assert.match(root.innerHTML,/aria-expanded="false"/);assert.match(root.innerHTML,/1435 Broadway/)});
check('map result choices keep their numbered point action',()=>{const html=render({active:true,role:'destination',stage:'points',origin:a,base:{kind:'station',name:'Canal St'},points:[{id:'entrance',name:'Varick Street'}]});assert.match(html,/data-planner-point="entrance"/);assert.match(html,/Choose a station entrance/);assert.doesNotMatch(html,/data-planner="submit"/)});
console.log(n+' picker checks passed');

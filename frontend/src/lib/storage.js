import {blankBrief,normalizeBrief} from './composition.js';
export {normalizeBrief};
export const key='sonora.workspace.preview.v1';
export const initial={version:1,activeProjectId:null,activeView:'studio',drafts:[],projects:[],brief:{...blankBrief},preferences:{autosaveDrafts:true,compactLibrary:false,reducedMotion:false}};
export function load(){
  try {
    const raw=localStorage.getItem(key);
    if(!raw)return {state:initial,available:true};
    const x=JSON.parse(raw);
    if(!x || x.version!==1)throw Error('Unsupported workspace');
    const records=value=>Array.isArray(value)?value.filter(r=>r && typeof r==='object' && !Array.isArray(r)):[];
    return {state:{...initial,...x,activeView:['studio','library','projects','settings'].includes(x.activeView)?x.activeView:'studio',preferences:{...initial.preferences,...x.preferences},brief:normalizeBrief(x.brief),drafts:records(x.drafts).map(normalizeBrief),projects:records(x.projects).map(p=>({...p,brief:normalizeBrief(p.brief)}))},available:true};
  }catch{return {state:initial,available:false};}
}
export function save(state){try{localStorage.setItem(key,JSON.stringify(state));return true}catch{return false}}

import {normalizeBrief,energyLabel} from './composition.js';import {arrangementPrompt} from './arrangement.js';import {characterCount} from './generationContract.js';
export const defaultRecipe={style:true,emotion:true,energy:true,tempo:true,instruments:true,vocals:true,production:true,arrangement:'detailed'};
export function normalizeRecipe(value){const v=value&&typeof value==='object'?value:{};return Object.fromEntries(Object.entries(defaultRecipe).map(([k,d])=>[k,k==='arrangement'?(['off','outline','detailed'].includes(v[k])?v[k]:d):(typeof v[k]==='boolean'?v[k]:d)]));}
export function promptPlan(value,{includeReference=false}={}){const b=normalizeBrief(value),r=normalizeRecipe(b.promptBuilder?.recipe);const candidates=[
 ['description','Core description',true,b.prompt.trim(),true],
 ['style','Style',r.style,`Style: ${b.mood}.`],['emotion','Emotion',r.emotion,`Emotion: ${b.emotion}.`],
 ['instruments','Instruments',r.instruments,b.instruments.length?`Instruments: ${b.instruments.join(', ')}.`:''],
 ['energy','Energy',r.energy,`Energy: ${energyLabel(b.energy).toLowerCase()}.`],
 ['tempo','Tempo / BPM',r.tempo,b.tempo!=='No preference'||b.bpm?`Tempo: ${b.tempo==='No preference'?'':b.tempo.toLowerCase()}${b.bpm?`${b.tempo==='No preference'?'':' / '}${b.bpm} BPM`:''}.`:''],
 ['vocals','Vocal direction / mode',r.vocals,b.instrumental?'Instrumental; no vocals.':[b.vocalDirection.trim()?`Voice: ${b.vocalDirection.trim()}.`:'',b.language.trim()?`Language: ${b.language.trim()}.`:''].filter(Boolean).join(' ')],
 ['production','Production notes',r.production,b.productionNotes.trim()?`Production: ${b.productionNotes.trim()}`:''],
 ['arrangement',r.arrangement==='outline'?'Arrangement outline':'Arrangement detail',r.arrangement!=='off',r.arrangement==='outline'&&b.arrangement.length?'Arrangement intention: '+b.arrangement.map(s=>`${s.name} (${s.intensity}/100)`).join(' → ')+'.':arrangementPrompt(b.arrangement)],
 ['reference','Creative reference',includeReference,b.reference.trim()?`Creative reference: ${b.reference.trim()}`:'']
 ];let compiled='';const components=candidates.map(([id,label,included,text,required=false])=>{const active=included&&!!text;const contribution=active?(compiled?' ':'')+text:'';compiled+=contribution;return {id,label,included,required,text,characterCount:characterCount(contribution),textCharacterCount:characterCount(text),available:!!text};});const total=characterCount(compiled);return {components,text:compiled,total,remaining:512-total,overLimit:Math.max(0,total-512)};}

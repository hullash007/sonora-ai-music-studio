export const styles = ['Cinematic','Ambient','Acoustic','Classical','Orchestral','Electronic','Jazz','Folk','Rock','Hip-hop','Experimental','Other'];
export const emotions = ['Reflective','Serene','Melancholic','Mysterious','Tense','Hopeful','Triumphant','Nostalgic','Dreamlike'];
export const tempos = ['No preference','Slow','Moderate','Fast','Driving'];
export const instruments = ['Piano','Strings','Synthesizers','Guitar','Bass','Percussion','Drums','Woodwinds','Brass','Choir','Other'];
export const blankBrief = {title:'',prompt:'',reference:'',mood:'Cinematic',emotion:'Reflective',energy:50,tempo:'No preference',bpm:null,instruments:[],instrumental:true,lyrics:'',vocalDirection:'',language:'',duration:30,productionNotes:'',promptBuilder:{text:null,source:'',includeReference:false,manual:false}};
const text = (v,n) => typeof v === 'string' ? v.slice(0,n) : '';
export function normalizeBrief(value) {
  const v = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const bpm = Number(v.bpm);
  return {...v,...blankBrief,
    title:text(v.title,80),prompt:text(v.prompt,512),reference:text(v.reference,240),
    mood:text(v.mood,80)||blankBrief.mood,emotion:text(v.emotion,80)||blankBrief.emotion,
    energy:typeof v.energy==='number' && Number.isFinite(v.energy)?Math.min(100,Math.max(0,Math.round(v.energy))):50,
    tempo:tempos.includes(v.tempo)?v.tempo:'No preference',
    bpm:v.bpm!==null && v.bpm!=='' && Number.isInteger(bpm) && bpm>=50 && bpm<=200?bpm:null,
    instruments:Array.isArray(v.instruments)?[...new Set(v.instruments.filter(x=>typeof x==='string' && x.trim()).map(x=>x.slice(0,80)))].slice(0,20):[],
    instrumental:typeof v.instrumental==='boolean'?v.instrumental:true,lyrics:text(v.lyrics,4096),
    vocalDirection:text(v.vocalDirection,240),language:text(v.language,80),
    duration:[30,60,90,120].includes(v.duration)?v.duration:30,productionNotes:text(v.productionNotes,500),
    promptBuilder:v.promptBuilder&&typeof v.promptBuilder==='object'?{text:typeof v.promptBuilder.text==='string'?v.promptBuilder.text:null,source:typeof v.promptBuilder.source==='string'?v.promptBuilder.source:'',includeReference:v.promptBuilder.includeReference===true,manual:v.promptBuilder.manual===true}:{...blankBrief.promptBuilder}
  };
}
export function energyLabel(value) { return ['Calm','Gentle','Moderate','Powerful','Intense'][Math.min(4,Math.floor(value/20.01))]; }
export function validateBrief(value) {
  const errors={};
  if(!value.prompt?.trim()) errors.prompt='Add a creative description before saving.';
  if(value.bpm!==null && value.bpm!=='' && (!Number.isInteger(Number(value.bpm)) || Number(value.bpm)<50 || Number(value.bpm)>200)) errors.bpm='Use a whole number from 50 to 200, or leave BPM blank.';
  return errors;
}
export function compositionText(value) {
  const b=normalizeBrief(value);
  const sections=[['SONORA COMPOSITION BRIEF',b.title.trim()||'Untitled composition'],['IDEA',b.prompt.trim()||'Add your creative description.']];
  if(b.reference.trim())sections.push(['CREATIVE REFERENCE',b.reference.trim()]);
  sections.push(['STYLE',b.mood],['EMOTION',b.emotion],['ENERGY',`${energyLabel(b.energy)} (${b.energy}/100)`],['TEMPO',b.tempo+(b.bpm?` · ${b.bpm} BPM`:'')],['INSTRUMENTATION',b.instruments.length?b.instruments.join(', '):'Open to interpretation'],['MODE',b.instrumental?'Instrumental':'Vocal'],['DURATION',`${b.duration} seconds`]);
  if(!b.instrumental){if(b.language.trim())sections.push(['LANGUAGE',b.language.trim()]);if(b.vocalDirection.trim())sections.push(['VOCAL DIRECTION',b.vocalDirection.trim()]);if(b.lyrics.trim())sections.push(['LYRICS',b.lyrics.trim()]);}
  if(b.productionNotes.trim())sections.push(['PRODUCTION NOTES',b.productionNotes.trim()]);
  return sections.map(([label,value])=>`${label}\n${value}`).join('\n\n')+'\n\nCreative preferences only. Saved locally; no audio has been generated.';
}

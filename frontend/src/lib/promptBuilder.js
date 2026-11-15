import {characterCount} from './generationContract.js';
import {normalizeBrief,energyLabel} from './composition.js';
export function buildEnginePrompt(value,{includeReference=false}={}) {
  const b=normalizeBrief(value);
  const parts=[b.prompt.trim()];
  parts.push(`Style: ${b.mood}.`,`Emotion: ${b.emotion}.`);
  if(b.instruments.length)parts.push(`Instruments: ${b.instruments.join(', ')}.`);
  parts.push(`Energy: ${energyLabel(b.energy).toLowerCase()}.`);
  if(b.tempo!=='No preference'||b.bpm)parts.push(`Tempo: ${b.tempo==='No preference'?'':b.tempo.toLowerCase()}${b.bpm?`${b.tempo==='No preference'?'':' / '}${b.bpm} BPM`:''}.`);
  if(b.instrumental)parts.push('Instrumental; no vocals.');
  else {if(b.vocalDirection.trim())parts.push(`Voice: ${b.vocalDirection.trim()}.`);if(b.language.trim())parts.push(`Language: ${b.language.trim()}.`);}
  if(b.productionNotes.trim())parts.push(`Production: ${b.productionNotes.trim()}`);
  if(includeReference&&b.reference.trim())parts.push(`Creative reference: ${b.reference.trim()}`);
  return parts.filter(Boolean).join(' ');
}
export function promptIdentity(brief,includeReference=false){return buildEnginePrompt(brief,{includeReference});}
export function previewPayload(brief,prompt){const b=normalizeBrief(brief);return {prompt,lyrics:b.instrumental?'':b.lyrics,duration:b.duration};}
export function promptErrors(prompt){const errors=[];if(!prompt.trim())errors.push('Write or build a prompt first.');if(characterCount(prompt.trim())>512)errors.push(`${characterCount(prompt.trim())-512} characters over the 512-character engine limit. Shorten the prompt; your full brief is kept.`);return errors;}
export const templates=[
 {id:'cinematic',name:'Cinematic worldbuilding',category:'WIDE / NARRATIVE',description:'A world with room to unfold.',brief:{title:'',prompt:'A vast landscape slowly revealing itself. Distant motifs return with growing warmth as the horizon opens.',mood:'Cinematic',emotion:'Hopeful',energy:55,tempo:'Slow',instruments:['Strings','Brass','Percussion'],instrumental:true,productionNotes:'Expansive space, a restrained opening and a gradual narrative build.',duration:90}},
 {id:'ancient',name:'Ancient civilization',category:'TIME / TEXTURE',description:'Stone, sunlight and a distant pulse.',brief:{title:'',prompt:'An ancient city awakening at dawn. Low strings, distant horns and a quiet ceremonial pulse suggest a lost world returning to life.',mood:'Orchestral',emotion:'Mysterious',energy:50,tempo:'Slow',instruments:['Strings','Brass','Percussion'],instrumental:true,productionNotes:'Warm acoustic textures, spacious room, no abrupt transitions.',duration:90}},
 {id:'piano',name:'Minimal piano',category:'CLOSE / INTIMATE',description:'A small melody. Nothing to hide behind.',brief:{title:'',prompt:'A solitary piano in a quiet room. A simple melody repeats with small changes, leaving silence between each phrase.',mood:'Classical',emotion:'Reflective',energy:20,tempo:'Slow',instruments:['Piano'],instrumental:true,productionNotes:'Close and natural, soft dynamics, very little accompaniment.',duration:60}},
 {id:'ambient',name:'Ambient exploration',category:'SPACE / STILLNESS',description:'Textures that move like weather.',brief:{title:'',prompt:'An unhurried drift through a changing atmosphere. Soft tones emerge, overlap and fade without a strong beat.',mood:'Ambient',emotion:'Dreamlike',energy:15,tempo:'No preference',instruments:['Synthesizers'],instrumental:true,productionNotes:'Long evolving textures, gentle swells, open stereo space.',duration:120}},
 {id:'electronic',name:'Dark electronic',category:'PULSE / SHADOW',description:'A measured rhythm beneath the surface.',brief:{title:'',prompt:'A nocturnal electronic piece with a steady low pulse, angular percussion and shifting layers of textured synthesizers.',mood:'Electronic',emotion:'Tense',energy:75,tempo:'Driving',bpm:110,instruments:['Synthesizers','Bass','Drums'],instrumental:true,productionNotes:'Controlled low end, rhythmic detail, a gradual increase in density.',duration:90}},
 {id:'chamber',name:'Classical chamber',category:'WOOD / STRING',description:'A conversation among a few voices.',brief:{title:'',prompt:'A small acoustic ensemble trading a lyrical motif. Bowed strings and a woodwind answer one another with restrained expression.',mood:'Classical',emotion:'Nostalgic',energy:40,tempo:'Moderate',instruments:['Strings','Woodwinds'],instrumental:true,productionNotes:'Natural room, clear phrasing, intimate ensemble balance.',duration:90}}
];

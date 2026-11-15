import {stagingProfile} from './engineProfile.js';
// Python len counts Unicode code points, unlike JS string.length's UTF-16 units.
export const characterCount=value=>typeof value==='string'?[...value].length:0;
export function buildGenerationPayload(brief,enginePrompt){return {prompt:enginePrompt,lyrics:brief.instrumental?'':brief.lyrics,duration:brief.duration};}
export function validateGenerationRequest(brief,enginePrompt){
 const payload=buildGenerationPayload(brief,enginePrompt),errors={};
 const promptLength=characterCount(typeof payload.prompt==='string'?payload.prompt.trim():payload.prompt),lyricsLength=characterCount(payload.lyrics);
 if(typeof payload.prompt!=='string')errors.prompt='Prompt must be text.';
 else if(!payload.prompt.trim())errors.prompt='A music prompt is required.';
 else if(promptLength>512)errors.prompt=`Remove ${promptLength-512} characters to meet the 512-character engine limit. The full text is kept.`;
 if(typeof payload.lyrics!=='string')errors.lyrics='Lyrics must be text.';
 else if(lyricsLength>4096)errors.lyrics=`Lyrics are ${lyricsLength-4096} characters over the 4,096-character limit.`;
 if(!Number.isInteger(payload.duration)||payload.duration<10||payload.duration>480)errors.duration='Duration must be a whole number from 10 to 480 seconds.';
 return {valid:Object.keys(errors).length===0,errors,payload,promptLength,lyricsLength,duration:payload.duration};
}
export function getGenerationCompatibility(validation,profile=stagingProfile){
 const durationCompatible=!validation.errors.duration&&(profile.fixedDurationSeconds==null||validation.duration===profile.fixedDurationSeconds);
 return {apiValid:validation.valid,durationCompatible,profileCompatible:validation.valid&&durationCompatible,generationEnabled:profile.generationEnabled,canSubmit:false,reason:!durationCompatible?`This staging profile accepts ${profile.fixedDurationSeconds} seconds only. Your composition is kept as written.`:'Duration matches the documented staging profile.'};
}

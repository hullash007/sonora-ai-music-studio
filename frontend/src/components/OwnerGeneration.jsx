import React,{useEffect,useState} from 'react';
import {ownerRequest,isPublic,audioBlob} from '../lib/api';
import {buildEnginePrompt} from '../lib/promptBuilder';
const key='sonora-shared-job.'+(window.sonoraWorkspace||'anonymous');
export default function OwnerGeneration({brief,onNavigate}){
 const [lengths,setLengths]=useState([30]);
 useEffect(()=>{if(isPublic)return;let live=true;ownerRequest('/api/shared-studio').then(d=>{if(live&&Array.isArray(d.supported_durations))setLengths(d.supported_durations)}).catch(()=>{});return()=>{live=false}},[]);
 const duration=brief.duration;
 const [job,setJob]=useState(()=>localStorage.getItem(key)||''),[stage,setStage]=useState('Ready when you are.'),[busy,setBusy]=useState(false),[audio,setAudio]=useState('');
 const prompt=buildEnginePrompt(brief),lyrics=brief.instrumental?'[Instrumental]':brief.lyrics;
 const lengthAvailable=lengths.includes(duration);
 const valid=lengthAvailable&&brief.prompt.trim().length>0&&prompt.trim().length>0&&[...prompt.trim()].length<=512&&typeof lyrics==='string'&&[...lyrics].length<=4096;
 useEffect(()=>{if(!job||isPublic)return;let live=true,timer;setBusy(true);async function poll(){try{const d=await ownerRequest('/api/status/'+encodeURIComponent(job));if(!live)return;setStage(['queued','running','generating'].includes(d.status)?'Your track is in progress. This may take a few minutes.':d.stage||d.status);if(d.status==='complete'){const u=await audioBlob(job);if(live){setAudio(u);setBusy(false);setStage('Your track is ready. Listen now or find it in your library.')}return}if(['failed','cancelled','reconciliation_required','audio_commit_unknown'].includes(d.status)){setBusy(false);setStage('We could not finish this track. Please try again later.');return}}catch(e){if(live){setBusy(false);setStage('We could not check your track. Reload to see its latest progress.')}return}if(live)timer=setTimeout(poll,5000)}poll();return()=>{live=false;clearTimeout(timer)}},[job]);
 if(isPublic)return <p className="preview-generation">Generation is off in this preview. You can explore the editor and save drafts.</p>;
 async function generate(){if(busy||!valid)return;setBusy(true);setStage('Submitting your music request...');try{const d=await ownerRequest('/api/generate',{method:'POST',body:JSON.stringify({prompt:prompt.trim(),lyrics,duration})});localStorage.setItem(key,d.job_id);setJob(d.job_id);setStage('Your track is on its way. This may take a few minutes.')}catch(e){setBusy(false);setStage(e.message)}}
 return <section className="generation-area" aria-label="Music generation">
 <button className="button primary generate-track" disabled={busy||!valid} onClick={generate}><span aria-hidden="true">{busy?<span className="spinner"/>:'✦'}</span>{busy?'Creating your track...':`Generate ${duration<60?duration+'-second':duration===90?'90-second':duration/60+'-minute'} track`}<span aria-hidden="true">↗</span></button>
 <p className="generation-hint">{isPublic?'Generation is off in this preview.':'Usually takes a few minutes. Completed tracks save to your private library.'}</p>
 {!lengthAvailable&&<p className="field-error" role="alert">This length is unavailable. Choose {lengths.join(', ')} seconds.</p>}
 {!valid&&lengthAvailable&&brief.prompt.trim()&&<p className="field-error" role="alert">Your combined prompt must fit 512 characters. Shorten the description or optional sound settings.</p>}
 <div className={'generation-status '+(busy?'is-busy':'')} role="status" aria-live="polite">{stage==='Ready when you are.'?(!brief.prompt.trim()?'Add a description to begin.':'Ready to generate.'):stage}</div>
 {audio&&<div className="track-result"><div><IconReady/> <strong>Your track is ready</strong></div><audio controls src={audio}/><div className="result-actions"><a className="button secondary" href={audio} download="sonora-track.mp3">Download MP3</a><button className="text-link" onClick={()=>onNavigate('library')}>View library ↗</button></div></div>}
 <details className="request-preview"><summary>View the prompt sent to the model</summary><pre>{prompt}</pre><p>{brief.instrumental?'Instrumental':'With lyrics'} · {duration} seconds</p></details>
 </section>
}
function IconReady(){return <span className="ready-check" aria-hidden="true">✓</span>}

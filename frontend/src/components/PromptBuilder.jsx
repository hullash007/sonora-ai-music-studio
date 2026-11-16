import TermHelp from './TermHelp';import React,{useState} from 'react';
import {characterCount} from '../lib/generationContract';import Icon from './Icon';import RequestPreflight from './RequestPreflight';import PromptBudget from './PromptBudget';
import {buildEnginePrompt,promptIdentity,promptErrors,previewPayload} from '../lib/promptBuilder';
export default function PromptBuilder({brief,setBrief,onNotify}) {
 const [confirmRebuild,setConfirmRebuild]=useState(false),[fallback,setFallback]=useState('');
 const editor=brief.promptBuilder||{text:null,source:'',includeReference:false,manual:false};
 const assembled=buildEnginePrompt(brief,{includeReference:editor.includeReference});
 const text=editor.text===null?assembled:editor.text;
 const stale=editor.text!==null&&editor.source!==promptIdentity(brief,editor.includeReference);
 const errors=promptErrors(text);
 function update(values){setBrief({...brief,promptBuilder:{...editor,...values}});}
 function rebuild(){update({text:assembled,source:promptIdentity(brief,editor.includeReference),manual:false});setConfirmRebuild(false);onNotify('Prompt rebuilt from your brief');}
 async function copy(value,label){try{await navigator.clipboard.writeText(value);onNotify(`${label} copied`);}catch{setFallback(value);onNotify('Clipboard unavailable. Select and copy the text below.');}}
 return <><section id="prompt-builder" tabIndex={-1} className="prompt-builder panel" aria-labelledby="prompt-builder-heading"><div className="section-heading"><div><span className="kicker">06 / PROMPT BUILDER</span><div className="term-heading"><h2 id="prompt-builder-heading">From intent to <em>words.</em></h2><TermHelp term="Engine prompt"/></div></div><span className="tag">LOCAL PROCESSING</span></div><p className="builder-intro">Assemble a description from your choices. Make it your own before using it elsewhere.</p>
 <label className="reference-choice"><input type="checkbox" checked={editor.includeReference} onChange={e=>update({includeReference:e.target.checked})}/><span>Include creative reference<small>Off by default. Your inspiration stays separate unless you choose to include it.</small></span></label>
 <PromptBudget brief={brief} onChange={update} enginePrompt={text}/>
 <details className="assembled-preview"><summary>Assembled from your brief <span>Deterministic, not AI-generated</span></summary><p>{assembled}</p></details>
 <div className="label-row"><label className="field-label" htmlFor="engine-prompt">Engine prompt <span>{editor.manual?'edited by you':'assembled locally'}</span></label><span className={errors.length?'over-limit':''}>{characterCount(text.trim())} / 512</span></div>
 <textarea id="engine-prompt" value={text} onChange={e=>update({text:e.target.value,source:editor.text===null?promptIdentity(brief,editor.includeReference):editor.source,manual:true})} aria-invalid={!!errors.length} aria-describedby="engine-prompt-status"/>
 <p id="engine-prompt-status" className={errors.length?'field-error':'prompt-valid'}>{errors.length?errors.join(' '):'Within the engine prompt limit. Nothing is sent.'}</p>
 {stale&&<div className="prompt-stale" role="status">Your composition changed. This prompt is kept as written. Rebuild when you are ready.</div>}
 <div className="builder-actions"><button className="button secondary" onClick={()=>editor.manual?setConfirmRebuild(true):rebuild()}>Rebuild prompt</button><button className="button primary" onClick={()=>copy(text,'Engine prompt')} disabled={!text.trim()}>Copy prompt <Icon name="arrow" size={15}/></button></div>
 {confirmRebuild&&<div className="inline-confirm" role="group" aria-label="Confirm prompt rebuild"><strong>Replace your manual prompt?</strong><p>Your composition brief will stay unchanged.</p><div><button className="button ghost" onClick={()=>setConfirmRebuild(false)}>Keep my edits</button><button className="button secondary" onClick={rebuild}>Replace with rebuilt prompt</button></div></div>}
 <details className="payload-preview"><summary>Engine-ready inputs <span>Local preview only</span></summary><p>Lyrics and duration remain separate. Instrumental mode sends no lyrics in this preview.</p><dl><div><dt>Duration</dt><dd>{brief.duration} seconds</dd></div><div><dt>Lyrics</dt><dd>{brief.instrumental?'Empty · instrumental':`${brief.lyrics.length} characters · kept separate`}</dd></div></dl><pre>{JSON.stringify(previewPayload(brief,text),null,2)}</pre><button className="button secondary" disabled={!!errors.length} onClick={()=>copy(JSON.stringify(previewPayload(brief,text),null,2),'Engine input preview')}>Copy input JSON</button></details>
 {fallback&&<label className="field-label">Copy manually<textarea aria-label="Copy prompt manually" value={fallback} readOnly onFocus={e=>e.target.select()}/></label>}
 <div className="builder-boundary"><Icon name="studio" size={18}/><p>No model, no request, no audio. This builder works entirely in your browser. Saved drafts and project edits keep your prompt.</p></div></section><RequestPreflight brief={brief} enginePrompt={text} onNotify={onNotify}/></>;
}

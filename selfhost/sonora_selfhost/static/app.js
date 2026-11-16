'use strict';
const $ = id => document.getElementById(id);
let modelsReady = false, submitting = false, active = false, requestKey = null, lastJobs = '';
const terminal = new Set(['complete','failed','acknowledged']);
const states = {queued:'Queued locally',submitting:'Submitting once',remote:'Generating on your server',complete:'Saved',failed:'Failed',uncertain:'Submission needs a check',retrieval_error:'Retrieval paused',paused:'Connection changed',acknowledged:'Locally acknowledged'};
function notice(text) { $('notice').textContent = text; $('notice').hidden = !text; }
async function api(path, options={}) {
  const response = await fetch(path, {...options, credentials:'same-origin', headers:{'Content-Type':'application/json',...options.headers}});
  let body; try { body = await response.json(); } catch { throw new Error('Studio response unavailable. Check that sonora start is still running.'); }
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Check the form inputs and try again.');
  return body;
}
function buttonState() { $('generate').disabled = !modelsReady || active || submitting; }
function element(tag, text, cls) { const node=document.createElement(tag); if(text !== undefined) node.textContent=text; if(cls) node.className=cls; return node; }
function renderJobs(jobs) {
  active = jobs.some(j => !terminal.has(j.status)); buttonState();
  const fingerprint = JSON.stringify(jobs);
  if (fingerprint === lastJobs) return;
  lastJobs = fingerprint;
  const savedCount=jobs.filter(j=>j.status==='complete').length;
  $('track-count').textContent = `${savedCount} ${savedCount===1?'track':'tracks'}`;
  if (!jobs.length) return;
  const fragment = document.createDocumentFragment();
  for (const job of jobs) {
    const card = element('article',undefined,'track'), info = element('div');
    const problem = ['uncertain','retrieval_error','paused','failed'].includes(job.status);
    info.append(element('span', states[job.status] || job.status, 'state ' + (problem?'problem':terminal.has(job.status)?'':'busy')));
    info.append(element('h3',job.prompt));
    const duration = job.actual_duration ? `${job.actual_duration.toFixed(1)}s` : `${job.duration}s requested`;
    info.append(element('p',`${job.model} · ${duration} · ${new Date(job.created*1000).toLocaleString()}`));
    card.append(info);
    const output=element('div');
    if (job.status === 'complete') {
      const audio=element('audio'); audio.controls=true; audio.preload='none'; audio.src=`/api/audio/${encodeURIComponent(job.id)}`; audio.setAttribute('aria-label',`Play ${job.prompt}`);
      const link=element('a','Download WAV'); link.href=audio.src; link.download=`sonora-${job.id.slice(0,8)}.wav`;
      output.append(audio,link);
    } else {
      const message=job.error || (job.status==='remote'?'Your server is working. This page only checks status, never resubmits.':job.status==='acknowledged'?'This local record was released. No remote task was cancelled.':'Keep this studio running. One track is submitted for this request.');
      output.append(element('p',message));
      if (job.remote_id) output.append(element('pre',`Remote ID: ${job.remote_id}`));
      if (problem && job.status!=='failed') output.append(element('pre',`Local ID: ${job.id}\nStop the studio, then inspect with sonora jobs.\nUse sonora reconcile ID for a known remote task.\nDo not generate again to retry a download.`));
    }
    card.append(output); fragment.append(card);
  }
  $('jobs').replaceChildren(fragment);
}
async function refresh() {
  try {
    const status=await api('/api/status'); $('endpoint').textContent=status.endpoint;
    const result=await api('/api/models'); const previous=$('model').value;
    $('model').replaceChildren(...result.models.map(model=>{const opt=element('option',model);opt.value=model;return opt;}));
    $('model').value=result.models.includes(previous)?previous:result.models.includes(result.selected)?result.selected:result.models[0];
    $('model').disabled=false; modelsReady=true; notice('');
  } catch(error) { modelsReady=false; $('model').disabled=true; notice(error.message); }
  buttonState();
}
async function poll() {
  try { renderJobs((await api('/api/jobs')).jobs); }
  catch(error) { notice(error.message); modelsReady=false; buttonState(); }
  finally { setTimeout(poll,3000); }
}
$('refresh').addEventListener('click', refresh);
$('create-form').addEventListener('submit',async event=>{
  event.preventDefault(); if(submitting||active||!modelsReady) return;
  if(!requestKey) requestKey=crypto.randomUUID();
  const body={request_key:requestKey,prompt:$('prompt').value,lyrics:$('lyrics').value,duration:Number($('duration').value),model:$('model').value,confirm_cost:$('cost').checked};
  submitting=true; buttonState(); notice('');
  try {
    await api('/api/generate',{method:'POST',body:JSON.stringify(body)});
    requestKey=null; $('cost').checked=false;
    renderJobs((await api('/api/jobs')).jobs);
  } catch(error) {
    // Keep the request ID after an ambiguous browser request. Same click cannot submit twice.
    notice(error.message + ' Check Your tracks before trying again.');
    try { renderJobs((await api('/api/jobs')).jobs); } catch {}
  } finally { submitting=false; buttonState(); }
});
(async()=>{
  const params=new URLSearchParams(location.hash.slice(1)), token=params.get('session');
  if(token){ history.replaceState(null,'',location.pathname);try {await api('/api/session',{method:'POST',headers:{'X-Sonora-Session':token}});} catch(error){notice(error.message);return;} }
  await refresh(); poll();
})();

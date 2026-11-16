let sessionPassword="";export const setPassword=x=>{sessionPassword=x};export const isPublic=document.getElementById("root").dataset.publicPreview!=="false";
export async function library(signal){const r=await fetch('/api/library',{signal,headers:isPublic?{}:{'X-Portal-Password':sessionPassword}});if(!r.ok)throw Error('The library could not be reached. Please try again.');return(await r.json()).tracks||[]}
export const audioUrl=id=>'/api/library/'+encodeURIComponent(id)+'/audio';

export async function audioBlob(id){const r=await fetch(audioUrl(id),{headers:isPublic?{}:{"X-Portal-Password":sessionPassword}});if(!r.ok)throw Error("Audio unavailable");return URL.createObjectURL(await r.blob())}
export async function ownerRequest(path, options={}){const r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Portal-Password':sessionPassword,...options.headers}});const d=await r.json();if(!r.ok)throw Error(d.detail||'Request failed');return d;}

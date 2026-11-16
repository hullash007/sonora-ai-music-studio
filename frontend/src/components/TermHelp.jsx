import React from 'react';import {glossary} from '../lib/onboarding';
export default function TermHelp({term}){return <details className="term-help"><summary aria-label={`About ${term.toLowerCase()}`}>i</summary><p>{glossary.find(([name])=>name===term)?.[1]}</p></details>}

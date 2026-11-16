"""Offline AST-only checks against extracted pinned upstream source.
Usage: python tests/audit/upstream_signature_probe.py SOURCE_DIR
SOURCE_DIR needs inference.py, model-download.py and llm.py from the
reviewed upstream commit. Does not import upstream modules or run models.
This checks keyword names only, not runtime compatibility or dependencies.
"""
import ast,json,pathlib,sys
root=pathlib.Path.cwd()
source=pathlib.Path(sys.argv[1])
worker=ast.parse((root/'infra/modal/sonora_staging.py').read_text())
upstreams={key:ast.parse((source/(file+'.py')).read_text()) for key,file in [('GenerationParams','inference'),('GenerationConfig','inference'),('generate_music','inference'),('ensure_model_downloaded','model-download'),('initialize','llm')]}
results=[]
for name,tree in upstreams.items():
 definition=next(n for n in ast.walk(tree) if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name==name)
 allowed={n.target.id for n in definition.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name)} if isinstance(definition,ast.ClassDef) else {a.arg for a in definition.args.args+definition.args.kwonlyargs}
 calls=[n for n in ast.walk(worker) if isinstance(n,ast.Call) and ((isinstance(n.func,ast.Name) and n.func.id==name) or (name=='initialize' and isinstance(n.func,ast.Attribute) and n.func.attr=='initialize'))]
 for call in calls:
  used={k.arg for k in call.keywords}
  assert not used-allowed,(name,used-allowed)
  results.append({'call':name,'keyword_arguments':sorted(used),'supported':True})
print(json.dumps(results,indent=2))

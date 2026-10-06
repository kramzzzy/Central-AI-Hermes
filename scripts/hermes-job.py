"""One isolated Hermes request, with server-selected tool access. JSON over stdio."""
import os,sys,json,contextlib
from pathlib import Path
# This non-interactive surface cannot answer terminal approval prompts. Hermes
# uses its configured unattended approval policy instead of hanging for input.
os.environ['HERMES_SESSION_PLATFORM']='api_server'
with contextlib.redirect_stdout(sys.stderr):
 from hermes_profile import load_profile
 home, config = load_profile()
 from hermes_cli.runtime_provider import resolve_runtime_provider
 from run_agent import AIAgent
 from hermes_runtime_policy import tool_options
 body=json.load(sys.stdin)
 mode=body.get('execution_mode','draft-only')
 options=tool_options(mode)
 requested_model=body.get('model')
 model=requested_model if requested_model and requested_model not in {'workspace-default','hermes'} else config['default']
 runtime=resolve_runtime_provider(target_model=model)
 kwargs={key:runtime[key] for key in ['provider','api_mode','base_url','api_key','acp_command','acp_args'] if key in runtime}
 agent=AIAgent(model=model,session_id=body.get('session_id'),**options,max_iterations=16 if mode=='tools' else 2,max_tokens=2400 if mode=='tools' else 1800,quiet_mode=True,skip_context_files=True,skip_memory=True,skip_background_review=True,load_soul_identity=False,save_trajectories=False,run_budget_seconds=180,**kwargs)
 # Fail closed if a Hermes upgrade changes empty-toolset semantics.
 if mode=='draft-only' and agent.tools: raise RuntimeError('Draft worker unexpectedly received tools')
 agent._persist_disabled=True
 messages=body['messages']
 system='\n'.join(m['content'] for m in messages if m['role']=='system')
 user_parts=[]
 for m in messages:
  if m['role']=='user':
   user_parts.extend(m['content'] if isinstance(m['content'],list) else [{'type':'text','text':m['content']}])
 prompt=user_parts if any(p.get('type')=='image_url' for p in user_parts) else '\n'.join(p['text'] for p in user_parts)
 result=agent.run_conversation(user_message=prompt,system_message=system)
 if not result.get('completed') or result.get('failed') or result.get('partial'): raise RuntimeError('Hermes turn did not complete')
 output=result.get('final_response')
 if not output: raise RuntimeError('No final response')
print(json.dumps({'model':model,'runtime_profile':os.environ['ORBIT_HERMES_PROFILE'],'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':output}}]}))

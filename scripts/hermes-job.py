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
 raw_default=config.get('default') or os.environ.get('HERMES_CHAT_MODEL') or 'deepseek/deepseek-chat-v3-0324'
 model=requested_model if requested_model and requested_model not in {'workspace-default','hermes'} else raw_default
 if 'deepseek-v4.1-flash' in str(model) or 'glm-5.2' in str(model):
  model='deepseek/deepseek-chat-v3-0324'
 runtime=resolve_runtime_provider(requested=config.get('provider'),target_model=model)
 kwargs={key:runtime[key] for key in ['provider','api_mode','base_url','api_key','acp_command','acp_args'] if key in runtime}
 if not kwargs.get('api_key') and os.environ.get('OPENROUTER_API_KEY'):
  kwargs['provider']=kwargs.get('provider') or 'openrouter'
  kwargs['base_url']=kwargs.get('base_url') or 'https://openrouter.ai/api/v1'
  kwargs['api_mode']=kwargs.get('api_mode') or 'chat_completions'
  kwargs['api_key']=os.environ['OPENROUTER_API_KEY']
 max_tok=int(body.get('max_tokens') or 4096)
 agent=AIAgent(model=model,session_id=body.get('session_id'),**options,max_iterations=16 if mode=='tools' else 2,max_tokens=max_tok,quiet_mode=True,skip_context_files=True,skip_memory=True,skip_background_review=True,load_soul_identity=False,save_trajectories=False,run_budget_seconds=180,**kwargs)
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
 if isinstance(prompt,str) and prompt.strip() and 'OFFICIAL YOUR CHOICE SOLAR WEBSITE CALCULATOR' not in system:
  try:
   from hermes_plugins.central_ai_whatsapp.adapter import _calculate_solar_pre_assessment
   solar_calc_block=_calculate_solar_pre_assessment(prompt)
   if solar_calc_block:
    system=(system+'\n\n'+solar_calc_block).strip()
  except Exception:
   pass
 result=agent.run_conversation(user_message=prompt,system_message=system)
 output=(result.get('final_response') or '').strip()
 if result.get('failed') or (not result.get('completed') and not output): raise RuntimeError('Hermes turn did not complete')
 if not output: raise RuntimeError('No final response')
print(json.dumps({'model':model,'runtime_profile':os.environ['ORBIT_HERMES_PROFILE'],'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':output}}]}))

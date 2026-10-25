# Graph Report - Central-AI-Hermes  (2026-10-24)

## Corpus Check
- 152 files · ~131,807 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 13 file(s) not represented in the graph (top: (none) 5, .example 2, .patch 2)

## Summary
- 1990 nodes · 5348 edges · 98 communities (77 shown, 21 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 393 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `71b359e1`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- BackgroundTasks
- NativeChat
- production-bootstrap.mjs
- MemoryControls
- laya_embedding_service.py
- main.go
- OSCalls
- re
- FishCall
- assistant_instructions.py
- testing.T
- BackupError
- hermes_google.py
- json
- coolify_voice.py
- answerIncomingWithVoice
- os
- hermes-chat-entry.py
- StreamTests
- main
- context.Context
- relayFanout
- hermes_runtime_policy.py
- hermes_laya.py
- VoiceTests
- textBridge
- .test_resume_is_read_only_and_does_not_reload_history
- pcm
- local-bridge.py
- StreamPhoneCall
- hermes_settings.py
- Reader
- FishRestSpeech
- whatsapp-call-voice.py
- callback.go
- SocialWorkspace
- AGENTS.md
- fakeRelay
- adapter.py
- Handler
- .do_POST
- whatsapp_system_context.py
- pairing.go
- worker.ts
- get_catalog_models
- TurnControl
- hermes_artwork.py
- central_ai_service.py
- .connectFilter
- build_engine_adapter
- hermes_os.py
- SpecialistPool
- stream_conversation.py
- ConversationModelTests
- register
- Central AI — Hermes Intelligence & Superpowers Stack (Box 1)
- first-run-ui.js
- BridgeTests
- StreamingTurn
- laya-router-service.py
- sys
- CloudRecognizer
- .prepare
- Follow-up tracker
- Meeting preparation
- Morning briefing
- Personal assistant booking
- Receipts and expenses
- Team coordination
- read_weather
- Handler
- Production Build Recipes & Deployment Architecture
- probe.go
- whatsapp_fish.py
- .test_canceled_event_batch_cannot_emit_or_finish_a_reply
- TextModelProfileTests
- NativeTaskBridge
- client.py
- FreeSpeech
- run
- PlaybackTests
- CallbackIntentTests
- IdleThread
- install_process_token
- SpeculativeReply
- calculate_solar_planning_v2
- central_ai_whatsapp/__init__.py
- Flagship Commercial Ads (`/ads`)
- Marketing (`/marketing`)
- Flagship Product Showcase (`/product`)
- Promotion (`/promotion`)
- SetupError
- ExportError
- _app_os_urls
- Response
- manage_communications

## God Nodes (most connected - your core abstractions)
1. `SocialWorkspace` - 46 edges
2. `NativeChat` - 45 edges
3. `BackgroundTasks` - 40 edges
4. `SocialError` - 38 edges
5. `SocialTests` - 32 edges
6. `FishCall` - 30 edges
7. `main()` - 28 edges
8. `StreamPhoneCall` - 27 edges
9. `voiceAudio` - 25 edges
10. `atomic_write()` - 24 edges

## Surprising Connections (you probably didn't know these)
- `Assistant Identity & Voice Call Architecture` --references--> `call_whatsapp_contact()`  [INFERRED]
  AGENTS.md → scripts/hermes-os-mcp.py
- `social_media()` --uses--> `SocialWorkspace`  [INFERRED]
  hermes_plugins/central_ai_app_os/client.py → scripts/hermes_social.py
- `_worker()` --indirect_call--> `_retain_memory_item()`  [INFERRED]
  scripts/local-bridge.py → hermes_plugins/central_ai_whatsapp/adapter.py
- `_build_message_event()` --calls--> `attach_assistant_skills()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/whatsapp_system_context.py
- `_build_message_event()` --calls--> `get_whatsapp_reply_style()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/whatsapp_system_context.py

## Import Cycles
- None detected.

## Communities (98 total, 21 thin omitted)

### Community 0 - "BackgroundTasks"
Cohesion: 0.07
Nodes (7): TaskTests, factory(), CallbackTests, FishService, start(), BackgroundTasks, session_ready()

### Community 1 - "NativeChat"
Cohesion: 0.07
Nodes (12): NativeChat, Installation, CallTaskChat, delivery_time(), next_time(), RoutineError, Routines, check_account() (+4 more)

### Community 2 - "production-bootstrap.mjs"
Cohesion: 0.06
Nodes (52): databaseSettings(), field(), httpsOrigin(), modelSettings(), providers, SetupError, verifyProvider(), checkDatabase() (+44 more)

### Community 3 - "MemoryControls"
Cohesion: 0.30
Nodes (3): MemoryControls, MemoryError, revision()

### Community 4 - "laya_embedding_service.py"
Cohesion: 0.12
Nodes (12): get(), check_auth(), classify_single(), embed_texts(), ensure_model_files(), get_cached_criteria_embeddings(), health(), load_engine() (+4 more)

### Community 5 - "main.go"
Cohesion: 0.08
Nodes (6): engineHealthy(), TestIncomingRelayProbeDoesNotEndorseUnavailableEndpoint(), TestEngineCannotConnectBeforeExplicitEnableMarker(), TestEngineHealthRequiresActualWhatsAppConnection(), waitEngineEnabled(), ptr()

### Community 6 - "OSCalls"
Cohesion: 0.17
Nodes (4): OSCalls, dispatch(), reconcile(), NativeVoiceConversation

### Community 7 - "re"
Cohesion: 0.09
Nodes (8): is_correction(), is_task_cancellation(), resolve_contact_target(), voice_reasoning_effort(), read_marker(), restore_installation(), preserved_profiles(), context_name()

### Community 8 - "FishCall"
Cohesion: 0.10
Nodes (5): fish_api(), SpeechGate, FeedTests, GateTests, FishCall

### Community 9 - "assistant_instructions.py"
Cohesion: 0.16
Nodes (8): installed(), profile_home(), identity_text(), rename_profile(), atomic_write(), profile_lock(), update_profile_instructions(), _discover_disk_skills()

### Community 10 - "testing.T"
Cohesion: 0.12
Nodes (31): TestContinuousFilterWireAndReconnectKeepCallAlive(), TestFilterRequiresExactCallCredentials(), conversationFixture(), eventually(), TestBargeInDropsOldAudioAndKeepsOpeningWords(), TestContinuousReceiveCarriesAudioWhileReplyIsBusy(), TestFailureKeepsCallAliveAndNextTurnWorksWithoutReplay(), TestInterruptMergesOnlyUnsubmittedUtterances() (+23 more)

### Community 11 - "BackupError"
Cohesion: 0.19
Nodes (8): BackupError, Backups, redirect_request(), work(), serve(), do_GET(), do_POST(), reply()

### Community 12 - "hermes_google.py"
Cohesion: 0.16
Nodes (13): extract_email_content(), extract_parts(), GoogleError, GoogleWorkspace, fetch_single(), page(), identifier(), identity() (+5 more)

### Community 13 - "json"
Cohesion: 0.08
Nodes (23): social_media(), add_knowledge(), call_contact(), call_whatsapp_contact(), close_widget(), control_widget(), create_task(), delegate_specialists() (+15 more)

### Community 14 - "coolify_voice.py"
Cohesion: 0.12
Nodes (29): atomic_write(), apply(), config(), configured(), deployment_phase(), deployment_status(), fingerprint(), NoRedirect (+21 more)

### Community 16 - "answerIncomingWithVoice"
Cohesion: 0.11
Nodes (6): answerIncoming(), answerIncomingWithVoice(), TestIncomingAnswersBeforeSlowVoiceAndRetainsEarlyReady(), TestIncomingHangupCancelsPendingVoicePreparation(), fakeIncoming, incomingHandle

### Community 17 - "os"
Cohesion: 0.09
Nodes (15): native_export(), slugify_handle(), context(), command(), export(), filehash(), recovery(), serve() (+7 more)

### Community 18 - "hermes-chat-entry.py"
Cohesion: 0.14
Nodes (3): configure_business_phone_runtime(), configure_phone_tools(), restrict_native_specialist_runtime()

### Community 19 - "StreamTests"
Cohesion: 0.11
Nodes (3): CallbackHTTPTests, StreamTests, pending()

### Community 20 - "main"
Cohesion: 0.14
Nodes (24): groupsHandler(), admittedCaller(), allowedPeer(), main(), readOutgoingRequest(), receiveStage(), cleanDigits(), configuredRouteAllowed() (+16 more)

### Community 21 - "context.Context"
Cohesion: 0.19
Nodes (8): voiceAudio, prepareVoice(), prepareVoiceFor(), prepareVoiceInternal(), prepareVoiceOutbound(), prepareVoiceWithCallback(), speechFrame, status

### Community 22 - "relayFanout"
Cohesion: 0.18
Nodes (7): newRelayFanout(), newRtpReplayFilter(), TestRtpReplayAcrossRelaysPreservesAudioPlayout(), relayFanout, relayMediaConnection, replayRing, rtpReplayFilter

### Community 23 - "hermes_runtime_policy.py"
Cohesion: 0.50
Nodes (3): load_config(), available_tools(), tool_options()

### Community 24 - "hermes_laya.py"
Cohesion: 0.27
Nodes (4): api_key(), available(), classify(), configured()

### Community 26 - "textBridge"
Cohesion: 0.13
Nodes (9): newTextBridge(), claimedCount(), TestTextAdmissionAndRouting(), TestTextReplyBindingAndSingleDelivery(), TestTextUncertainDeliveryCannotRetryAndClaimCannotReplay(), textFixture(), textRequest(), textBridge (+1 more)

### Community 27 - ".test_resume_is_read_only_and_does_not_reload_history"
Cohesion: 0.09
Nodes (3): NativeWarmupTests, owned_class(), TextBoundaryTests

### Community 28 - "pcm"
Cohesion: 0.19
Nodes (8): _cached_piper_voice(), native_enabled(), NativeSpeech, pcm(), tts_config(), warm_piper(), fixed_speech(), speech()

### Community 29 - "local-bridge.py"
Cohesion: 0.14
Nodes (20): _recall_memory_items(), _retain_memory_item(), export_authorized(), read_library(), complete(), validate(), profile_environment(), task_file_context() (+12 more)

### Community 30 - "StreamPhoneCall"
Cohesion: 0.19
Nodes (4): PhonePCM, StreamPhoneCall, output(), output()

### Community 31 - "hermes_settings.py"
Cohesion: 0.17
Nodes (11): RunProcesses, read_bank(), at(), main(), idle(), put(), revision(), run_settings() (+3 more)

### Community 34 - "whatsapp-call-voice.py"
Cohesion: 0.07
Nodes (16): central_ai_identity(), configure_identity(), get_business_knowledge_summary(), get_central_ai_identity(), get_company_name(), voice_call_note(), allowed_audio_call(), native_conversation() (+8 more)

### Community 35 - "callback.go"
Cohesion: 0.14
Nodes (16): callbackControl(), callbackTargetAllowed(), claimTaskCallback(), claimTaskCallbackUsing(), finishTaskCallback(), TestCallbackClaimsAreBoundToThePolledOwner(), TestCallbackConfigurationCannotRedirectToAnotherNumber(), TestCallbackDoesNotPreparePrivateReportBeforeActualPeerAnswer() (+8 more)

### Community 36 - "SocialWorkspace"
Cohesion: 0.07
Nodes (15): clean_str(), NoRedirect, provider_request(), SocialError, SocialWorkspace, save(), react(), send() (+7 more)

### Community 37 - "AGENTS.md"
Cohesion: 0.18
Nodes (10): Assistant Identity & Voice Call Architecture, graphify Knowledge Graph & Architecture Navigation, Hindsight Persistent Agent Memory, Mandatory Agent Communication Policy: Minimal, Concise & Precise, Mandatory Box 1 Dual-Push Synchronization (`Central-AI-Hermes`), Mandatory Main Assistant Control for Every New Feature, Strict Global Design Pattern & UI/UX Policy, Strict Prohibition: No Remote / Live Server Access (Unless User Explicitly Allows) (+2 more)

### Community 38 - "fakeRelay"
Cohesion: 0.33
Nodes (4): newFakeRelay(), TestRelayFanoutMigratesReceivesAndUnblocksOnClose(), TestRelayFanoutRefreshesOnlyMatchingAllocateAndBroadcastsMedia(), fakeRelay

### Community 39 - "adapter.py"
Cohesion: 0.21
Nodes (16): _build_message_event(), _calculate_solar_pre_assessment(), _extract_and_analyze_customer_lead(), _fetch_live_box3_context(), _format_natural_list(), _forward_customer_whatsapp_to_box3(), _has_customer_confirmed_details(), _is_premature_lead_handoff() (+8 more)

### Community 40 - "Handler"
Cohesion: 0.21
Nodes (3): BackupError, handle_backup(), Handler

### Community 41 - ".do_POST"
Cohesion: 0.06
Nodes (11): StreamingRecognizer, StreamingTurn, current_phone_chat(), Handler, incomplete_utterance(), native(), recognize(), stream_recognize() (+3 more)

### Community 42 - "whatsapp_system_context.py"
Cohesion: 0.21
Nodes (9): attach_assistant_skills(), attach_booking_skill(), get_channel_context(), get_configured_assistant_name(), get_whatsapp_reply_style(), install_booking_skill(), _native_health(), owner_channel_context() (+1 more)

### Community 43 - "pairing.go"
Cohesion: 0.24
Nodes (3): connectPairedDevice(), startPairing(), callProgressWriter

### Community 44 - "worker.ts"
Cohesion: 0.18
Nodes (5): calendarTimer, memoryTimer, pulse(), routinesTimer, timer

### Community 45 - "get_catalog_models"
Cohesion: 0.32
Nodes (3): _fetch_openrouter_models(), get_catalog_models(), _parse_model_entry()

### Community 46 - "TurnControl"
Cohesion: 0.16
Nodes (9): main(), call_greeting(), transcribe(), OSStreamVoice, emit(), text(), TurnCancelled, TurnControl (+1 more)

### Community 47 - "hermes_artwork.py"
Cohesion: 0.12
Nodes (12): ArtworkError, available(), _download_image_to_b64(), _extract_b64_from_data_uri(), generate(), generate_marketing_image(), _generate_procedural_avatar(), generate_social_image() (+4 more)

### Community 48 - "central_ai_service.py"
Cohesion: 0.23
Nodes (7): install_files(), profile_lock(), bootstrap(), install_memory(), install_whatsapp(), configure_profile(), _configure_profile()

### Community 50 - "build_engine_adapter"
Cohesion: 0.17
Nodes (16): _append_customer_sync_queue(), build_engine_adapter(), _complete(), connect(), _dispatch_inline_reply(), edit_message(), on_processing_complete(), _poll_messages() (+8 more)

### Community 51 - "hermes_os.py"
Cohesion: 0.31
Nodes (5): authenticated_exchange(), google_from_chat(), inspect_from_chat(), validate_inspection(), _without_app_blocks()

### Community 53 - "stream_conversation.py"
Cohesion: 0.15
Nodes (10): conversation_llm(), is_casual_greeting_intent(), normalize_voice_greeting(), Conversation, _is_valid_openrouter_voice_model(), openrouter_stream(), tool_definitions(), open_request() (+2 more)

### Community 55 - "register"
Cohesion: 0.50
Nodes (3): dashboard_origin(), register(), status()

### Community 56 - "Central AI — Hermes Intelligence & Superpowers Stack (Box 1)"
Cohesion: 0.22
Nodes (8): 🚀 1-Click Deployment on Coolify, 🏗️ Box 1 Architecture, Central AI — Hermes Intelligence & Superpowers Stack (Box 1), 🔒 Security & Decoupling Policy, 📦 Services Included, Step 1: Add Public Repository Resource, Step 2: Configure Environment Variables, Step 3: Click Deploy

### Community 57 - "first-run-ui.js"
Cohesion: 0.36
Nodes (6): api(), authDisplay(), install(), models, pollDeployment(), show()

### Community 61 - "sys"
Cohesion: 0.08
Nodes (10): integration_config(), managed_environment(), memory_config(), sync_profile(), field(), authorized(), complete(), _build_responses_kwargs() (+2 more)

### Community 62 - "CloudRecognizer"
Cohesion: 0.20
Nodes (3): CloudRecognizer, PreparedRecognition, run()

### Community 64 - "Follow-up tracker"
Cohesion: 0.29
Nodes (6): Follow-up tracker, Pitfalls, Prerequisites, Procedure, Verification, When to Use

### Community 65 - "Meeting preparation"
Cohesion: 0.29
Nodes (6): Meeting preparation, Pitfalls, Prerequisites, Procedure, Verification, When to Use

### Community 66 - "Morning briefing"
Cohesion: 0.29
Nodes (6): Morning briefing, Pitfalls, Prerequisites, Procedure, Verification, When to Use

### Community 67 - "Personal assistant booking"
Cohesion: 0.29
Nodes (6): Personal assistant booking, Pitfalls, Prerequisites, Procedure, Verification, When to Use

### Community 68 - "Receipts and expenses"
Cohesion: 0.29
Nodes (6): Pitfalls, Prerequisites, Procedure, Receipts and expenses, Verification, When to Use

### Community 69 - "Team coordination"
Cohesion: 0.29
Nodes (6): Pitfalls, Prerequisites, Procedure, Team coordination, Verification, When to Use

### Community 70 - "read_weather"
Cohesion: 0.26
Nodes (5): get_live_weather(), NoRedirect, read_weather(), get_live_weather(), WidgetWeatherTests

### Community 71 - "Handler"
Cohesion: 0.33
Nodes (3): connected(), finish(), Handler

### Community 72 - "Production Build Recipes & Deployment Architecture"
Cohesion: 0.40
Nodes (4): 3-Way Installation Sequence, Existing Box 1 installations: preserve data before the storage fix, Networking & Resolution, Production Build Recipes & Deployment Architecture

### Community 73 - "probe.go"
Cohesion: 0.20
Nodes (5): wireAudio(), probeAudio(), readProbeGreeting(), wireProbe(), outgoingRequest

### Community 74 - "whatsapp_fish.py"
Cohesion: 0.06
Nodes (16): is_goodbye_intent(), PhoneAudioFilter, speech_model(), start_audio_server(), handle(), SpeechPreferences, business_connection_limit(), is_callback_request() (+8 more)

### Community 77 - "NativeTaskBridge"
Cohesion: 0.20
Nodes (5): call_app_os_mcp(), call_whatsapp_action(), NativeTaskBridge, _resolve_hermes_api_key(), OSNativeTaskBridge

### Community 78 - "client.py"
Cohesion: 0.20
Nodes (28): add_knowledge(), _bridge_urls(), _call_app_os_mcp(), call_contact(), close_widget(), control_widget(), create_task(), end_call() (+20 more)

### Community 80 - "run"
Cohesion: 0.38
Nodes (6): filter_playback_input(), normalized(), words(), run(), _on_tool_pending(), speak()

### Community 84 - "install_process_token"
Cohesion: 0.20
Nodes (4): install_process_token(), WorkspaceToolsTests, client(), phone()

### Community 86 - "calculate_solar_planning_v2"
Cohesion: 0.33
Nodes (3): calculate_solar(), calculate_solar_planning_v2(), calculate_solar()

### Community 87 - "central_ai_whatsapp/__init__.py"
Cohesion: 0.52
Nodes (5): check_dependencies(), create_adapter(), register(), route_binding(), validate_config()

### Community 88 - "Flagship Commercial Ads (`/ads`)"
Cohesion: 0.40
Nodes (4): Flagship Commercial Ads (`/ads`), Required Output Structure for Every Ad, The MeiGen.ai 9-Layer Commercial Ad Prompt Architecture, When to Use

### Community 89 - "Marketing (`/marketing`)"
Cohesion: 0.40
Nodes (4): Mandatory Format for Campaign Files & Drafts, Marketing (`/marketing`), Multi-Angle Attention & Product Ad Framework, When to Use

### Community 90 - "Flagship Product Showcase (`/product`)"
Cohesion: 0.40
Nodes (4): Flagship Product Showcase (`/product`), Required Output Structure for Every Product Post, The 4 MeiGen.ai Product Poster Archetypes for Solar, When to Use

### Community 91 - "Promotion (`/promotion`)"
Cohesion: 0.40
Nodes (4): Core Principles: Straightforward Copy + Flagship Product Ad Visuals, Optional Product Poster Structure, Promotion (`/promotion`), When to Use

### Community 92 - "SetupError"
Cohesion: 0.31
Nodes (3): NoRedirect, SetupError, WhatsAppSetup

### Community 93 - "ExportError"
Cohesion: 0.33
Nodes (4): ExportError, progress(), backup_snapshot_window(), reserve()

### Community 94 - "_app_os_urls"
Cohesion: 0.33
Nodes (5): _app_os_urls(), _discovered_app_os_urls(), _read_env_key(), call_app_os_api(), inspect_workspace()

## Knowledge Gaps
- **21 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `BackgroundTasks` connect `BackgroundTasks` to `whatsapp_fish.py`, `StreamTests`, `OSCalls`, `re`?**
  _High betweenness centrality (0.032) - this node is a cross-community bridge._
- **Should `BackgroundTasks` be split into smaller, more focused modules?**
  _Cohesion score 0.07473684210526316 - nodes in this community are weakly interconnected._
- **Why does `SocialWorkspace` connect `SocialWorkspace` to `os`, `local-bridge.py`, `json`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Should `NativeChat` be split into smaller, more focused modules?**
  _Cohesion score 0.07367641614216956 - nodes in this community are weakly interconnected._
- **Why does `GoogleWorkspace` connect `hermes_google.py` to `local-bridge.py`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **Should `production-bootstrap.mjs` be split into smaller, more focused modules?**
  _Cohesion score 0.057297297297297295 - nodes in this community are weakly interconnected._
- **Should `laya_embedding_service.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11965811965811966 - nodes in this community are weakly interconnected._
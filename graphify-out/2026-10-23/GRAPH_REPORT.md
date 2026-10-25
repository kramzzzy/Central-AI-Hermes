# Graph Report - Central-AI-Hermes  (2026-10-23)

## Corpus Check
- 150 files · ~128,328 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 13 file(s) not represented in the graph (top: (none) 5, .example 2, .patch 2)

## Summary
- 1942 nodes · 5204 edges · 85 communities (72 shown, 13 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 379 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `b725d6ab`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- BackgroundTasks
- NativeChat
- production-bootstrap.mjs
- MemoryControls
- laya_embedding_service.py
- main.go
- os
- test_voice_stream.py
- FishCall
- sys
- testing.T
- BackupError
- hermes_google.py
- json
- coolify_voice.py
- pathlib
- answerIncomingWithVoice
- os-backup.py
- local-bridge.py
- StreamTests
- main
- context.Context
- relayFanout
- client.py
- re
- VoiceTests
- textBridge
- .test_resume_is_read_only_and_does_not_reload_history
- time
- fish_conversation.py
- StreamPhoneCall
- assistant_instructions.py
- Reader
- FreeSpeech
- whatsapp-call-voice.py
- callback.go
- SocialWorkspace
- AGENTS.md
- fakeRelay
- BridgeTests
- adapter.py
- OSCalls
- bridge_process.py
- worker.ts
- .do_POST
- TurnControl
- hermes_artwork.py
- hermes_backups.py
- build_engine_adapter
- SpecialistPool
- whatsapp-audio.py
- SpeculativeReply
- hermes_settings.py
- whatsapp-text-engine.py
- Central AI — Hermes Intelligence & Superpowers Stack (Box 1)
- first-run-ui.js
- SetupError
- StreamingTurn
- laya-router-service.py
- pairing.go
- CloudRecognizer
- .prepare
- Follow-up tracker
- Meeting preparation
- Morning briefing
- Personal assistant booking
- Receipts and expenses
- Team coordination
- read_weather
- first_run_auth.py
- Production Build Recipes & Deployment Architecture
- probe.go
- .do_POST
- test_text_model_profile.py
- .connectFilter
- central_ai_app_os/__init__.py
- get_catalog_models
- threading
- CallbackIntentTests
- native_export
- calculate_solar_planning_v2
- Flagship Commercial Ads (`/ads`)
- Marketing (`/marketing`)
- Flagship Product Showcase (`/product`)
- Promotion (`/promotion`)

## God Nodes (most connected - your core abstractions)
1. `NativeChat` - 45 edges
2. `SocialWorkspace` - 43 edges
3. `BackgroundTasks` - 40 edges
4. `SocialError` - 34 edges
5. `FishCall` - 30 edges
6. `main()` - 28 edges
7. `StreamPhoneCall` - 27 edges
8. `SocialTests` - 26 edges
9. `voiceAudio` - 25 edges
10. `atomic_write()` - 24 edges

## Surprising Connections (you probably didn't know these)
- `Assistant Identity & Voice Call Architecture` --references--> `call_whatsapp_contact()`  [INFERRED]
  AGENTS.md → scripts/hermes-os-mcp.py
- `social_media()` --uses--> `SocialWorkspace`  [INFERRED]
  hermes_plugins/central_ai_app_os/client.py → scripts/hermes_social.py
- `_build_message_event()` --calls--> `attach_assistant_skills()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/whatsapp_system_context.py
- `_build_message_event()` --calls--> `get_whatsapp_reply_style()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/whatsapp_system_context.py
- `_poll_messages()` --indirect_call--> `route()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/laya_embedding_service.py

## Import Cycles
- None detected.

## Communities (85 total, 13 thin omitted)

### Community 0 - "BackgroundTasks"
Cohesion: 0.10
Nodes (5): TaskTests, factory(), CallbackTests, BackgroundTasks, session_ready()

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

### Community 6 - "os"
Cohesion: 0.10
Nodes (15): install_files(), installed(), profile_home(), profile_lock(), bootstrap(), install_memory(), install_whatsapp(), authenticated_exchange() (+7 more)

### Community 7 - "test_voice_stream.py"
Cohesion: 0.07
Nodes (4): CancellationTests, PlaybackTests, ConversationModelTests, SpeechPreferences

### Community 8 - "FishCall"
Cohesion: 0.10
Nodes (4): SpeechGate, FeedTests, GateTests, FishCall

### Community 9 - "sys"
Cohesion: 0.06
Nodes (13): integration_config(), managed_environment(), memory_config(), sync_profile(), load_config(), field(), complete(), _build_responses_kwargs() (+5 more)

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
Cohesion: 0.07
Nodes (22): add_knowledge(), social_media(), add_knowledge(), call_contact(), call_whatsapp_contact(), close_widget(), control_widget(), create_task() (+14 more)

### Community 14 - "coolify_voice.py"
Cohesion: 0.12
Nodes (29): atomic_write(), apply(), config(), configured(), deployment_phase(), deployment_status(), fingerprint(), NoRedirect (+21 more)

### Community 15 - "pathlib"
Cohesion: 0.10
Nodes (4): read_marker(), restore_installation(), preserved_profiles(), context_name()

### Community 16 - "answerIncomingWithVoice"
Cohesion: 0.11
Nodes (6): answerIncoming(), answerIncomingWithVoice(), TestIncomingAnswersBeforeSlowVoiceAndRetainsEarlyReady(), TestIncomingHangupCancelsPendingVoicePreparation(), fakeIncoming, incomingHandle

### Community 17 - "os-backup.py"
Cohesion: 0.09
Nodes (16): authorized(), slugify_handle(), task_file_context(), context(), command(), export(), filehash(), recovery() (+8 more)

### Community 18 - "local-bridge.py"
Cohesion: 0.18
Nodes (15): _recall_memory_items(), api_key(), available(), classify(), configured(), _classify_customer_turn_laya(), _collect_customer_sync_items(), _ensure_customer_caller_enabled() (+7 more)

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

### Community 23 - "client.py"
Cohesion: 0.24
Nodes (17): _app_os_urls(), _call_app_os_mcp(), create_task(), _discovered_app_os_urls(), get_knowledge(), get_workspace_overview(), _hermes_api_key(), _inspect_from_snapshot() (+9 more)

### Community 24 - "re"
Cohesion: 0.09
Nodes (18): central_ai_identity(), configure_identity(), get_business_knowledge_summary(), get_central_ai_identity(), get_company_name(), is_correction(), is_task_cancellation(), voice_reasoning_effort() (+10 more)

### Community 26 - "textBridge"
Cohesion: 0.13
Nodes (9): newTextBridge(), claimedCount(), TestTextAdmissionAndRouting(), TestTextReplyBindingAndSingleDelivery(), TestTextUncertainDeliveryCannotRetryAndClaimCannotReplay(), textFixture(), textRequest(), textBridge (+1 more)

### Community 27 - ".test_resume_is_read_only_and_does_not_reload_history"
Cohesion: 0.09
Nodes (3): NativeWarmupTests, owned_class(), TextBoundaryTests

### Community 28 - "time"
Cohesion: 0.13
Nodes (8): _cached_piper_voice(), native_enabled(), NativeSpeech, pcm(), tts_config(), warm_piper(), fixed_speech(), speech()

### Community 29 - "fish_conversation.py"
Cohesion: 0.09
Nodes (19): call_app_os_mcp(), conversation_llm(), filter_playback_input(), normalized(), words(), fish_api(), is_casual_greeting_intent(), is_goodbye_intent() (+11 more)

### Community 30 - "StreamPhoneCall"
Cohesion: 0.19
Nodes (4): PhonePCM, StreamPhoneCall, output(), output()

### Community 31 - "assistant_instructions.py"
Cohesion: 0.22
Nodes (6): identity_text(), rename_profile(), atomic_write(), profile_lock(), update_profile_instructions(), _discover_disk_skills()

### Community 34 - "whatsapp-call-voice.py"
Cohesion: 0.10
Nodes (13): allowed_audio_call(), native_conversation(), private_phone_member(), task_native(), CallerTests, provision_business_phone(), provision_caller_phone(), provision_phone_profile() (+5 more)

### Community 35 - "callback.go"
Cohesion: 0.14
Nodes (16): callbackControl(), callbackTargetAllowed(), claimTaskCallback(), claimTaskCallbackUsing(), finishTaskCallback(), TestCallbackClaimsAreBoundToThePolledOwner(), TestCallbackConfigurationCannotRedirectToAnotherNumber(), TestCallbackDoesNotPreparePrivateReportBeforeActualPeerAnswer() (+8 more)

### Community 36 - "SocialWorkspace"
Cohesion: 0.08
Nodes (14): clean_str(), NoRedirect, provider_request(), SocialError, SocialWorkspace, react(), send(), send() (+6 more)

### Community 37 - "AGENTS.md"
Cohesion: 0.20
Nodes (9): Assistant Identity & Voice Call Architecture, graphify Knowledge Graph & Architecture Navigation, Hindsight Persistent Agent Memory, Mandatory Agent Communication Policy: Minimal, Concise & Precise, Mandatory Box 1 Dual-Push Synchronization (`Central-AI-Hermes`), Strict Global Design Pattern & UI/UX Policy, Strict Prohibition: No Remote / Live Server Access, Supabase & Database Layer Best Practices (RLS & Schema Security) (+1 more)

### Community 38 - "fakeRelay"
Cohesion: 0.33
Nodes (4): newFakeRelay(), TestRelayFanoutMigratesReceivesAndUnblocksOnClose(), TestRelayFanoutRefreshesOnlyMatchingAllocateAndBroadcastsMedia(), fakeRelay

### Community 39 - "BridgeTests"
Cohesion: 0.05
Nodes (9): call_whatsapp_action(), NativeTaskBridge, resolve_contact_target(), OSNativeTaskBridge, StreamingRecognizer, StreamingTurn, BridgeTests, FishService (+1 more)

### Community 40 - "adapter.py"
Cohesion: 0.17
Nodes (23): _append_customer_sync_queue(), _build_message_event(), edit_message(), _poll_messages(), send(), _calculate_solar_pre_assessment(), _extract_and_analyze_customer_lead(), _fetch_live_box3_context() (+15 more)

### Community 41 - "OSCalls"
Cohesion: 0.16
Nodes (4): OSCalls, dispatch(), reconcile(), NativeVoiceConversation

### Community 44 - "worker.ts"
Cohesion: 0.18
Nodes (5): calendarTimer, memoryTimer, pulse(), routinesTimer, timer

### Community 45 - ".do_POST"
Cohesion: 0.20
Nodes (7): export_authorized(), read_library(), complete(), validate(), profile_environment(), Handler, start_routine_tick()

### Community 46 - "TurnControl"
Cohesion: 0.16
Nodes (9): main(), call_greeting(), transcribe(), OSStreamVoice, emit(), text(), TurnCancelled, TurnControl (+1 more)

### Community 47 - "hermes_artwork.py"
Cohesion: 0.18
Nodes (10): ArtworkError, available(), _download_image_to_b64(), _extract_b64_from_data_uri(), generate(), generate_marketing_image(), _generate_procedural_avatar(), _NoRedirect (+2 more)

### Community 50 - "build_engine_adapter"
Cohesion: 0.14
Nodes (16): build_engine_adapter(), _complete(), connect(), _dispatch_inline_reply(), on_processing_complete(), _post_bridge_message(), _process_message_background(), _resolve_bridge_token() (+8 more)

### Community 52 - "whatsapp-audio.py"
Cohesion: 0.14
Nodes (4): PhoneAudioFilter, speech_model(), start_audio_server(), handle()

### Community 54 - "hermes_settings.py"
Cohesion: 0.24
Nodes (9): read_bank(), at(), main(), idle(), put(), revision(), runtime_settings(), SettingsError (+1 more)

### Community 55 - "whatsapp-text-engine.py"
Cohesion: 0.20
Nodes (5): install_process_token(), text_routes(), quiet_whatsapp_display(), _clean_poisoned_whatsapp_history(), run_route()

### Community 56 - "Central AI — Hermes Intelligence & Superpowers Stack (Box 1)"
Cohesion: 0.22
Nodes (8): 🚀 1-Click Deployment on Coolify, 🏗️ Box 1 Architecture, Central AI — Hermes Intelligence & Superpowers Stack (Box 1), 🔒 Security & Decoupling Policy, 📦 Services Included, Step 1: Add Public Repository Resource, Step 2: Configure Environment Variables, Step 3: Click Deploy

### Community 57 - "first-run-ui.js"
Cohesion: 0.36
Nodes (6): api(), authDisplay(), install(), models, pollDeployment(), show()

### Community 58 - "SetupError"
Cohesion: 0.31
Nodes (3): NoRedirect, SetupError, WhatsAppSetup

### Community 61 - "pairing.go"
Cohesion: 0.24
Nodes (3): connectPairedDevice(), startPairing(), callProgressWriter

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

### Community 71 - "first_run_auth.py"
Cohesion: 0.28
Nodes (3): connected(), finish(), Handler

### Community 72 - "Production Build Recipes & Deployment Architecture"
Cohesion: 0.40
Nodes (4): 3-Way Installation Sequence, Existing Box 1 installations: preserve data before the storage fix, Networking & Resolution, Production Build Recipes & Deployment Architecture

### Community 73 - "probe.go"
Cohesion: 0.20
Nodes (5): wireAudio(), probeAudio(), readProbeGreeting(), wireProbe(), outgoingRequest

### Community 74 - ".do_POST"
Cohesion: 0.08
Nodes (11): configure_business_phone_runtime(), configure_phone_tools(), current_phone_chat(), Handler, incomplete_utterance(), native(), recognize(), stream_recognize() (+3 more)

### Community 78 - "central_ai_app_os/__init__.py"
Cohesion: 0.22
Nodes (16): _bridge_urls(), call_contact(), close_widget(), control_widget(), end_call(), google_workspace(), inspection_available(), navigate_browser() (+8 more)

### Community 79 - "get_catalog_models"
Cohesion: 0.32
Nodes (3): _fetch_openrouter_models(), get_catalog_models(), _parse_model_entry()

### Community 80 - "threading"
Cohesion: 0.14
Nodes (6): fish_rest_tts(), openrouter_stream(), client(), open_request(), Response, response()

### Community 83 - "native_export"
Cohesion: 0.28
Nodes (5): ExportError, native_export(), progress(), backup_snapshot_window(), reserve()

### Community 86 - "calculate_solar_planning_v2"
Cohesion: 0.33
Nodes (3): calculate_solar(), calculate_solar_planning_v2(), calculate_solar()

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
Nodes (4): Core Principles: Straightforward Copy + Flagship Product Ad Visuals, Promotion (`/promotion`), Required Output Structure for Every Post, When to Use

## Knowledge Gaps
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `BackgroundTasks` connect `BackgroundTasks` to `test_voice_stream.py`, `BridgeTests`, `OSCalls`, `pathlib`, `StreamTests`, `whatsapp-audio.py`, `re`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Should `BackgroundTasks` be split into smaller, more focused modules?**
  _Cohesion score 0.09567307692307692 - nodes in this community are weakly interconnected._
- **Why does `SocialWorkspace` connect `SocialWorkspace` to `os-backup.py`, `local-bridge.py`, `json`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Should `NativeChat` be split into smaller, more focused modules?**
  _Cohesion score 0.0695970695970696 - nodes in this community are weakly interconnected._
- **Why does `FishCall` connect `FishCall` to `re`, `TurnControl`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Should `production-bootstrap.mjs` be split into smaller, more focused modules?**
  _Cohesion score 0.057297297297297295 - nodes in this community are weakly interconnected._
- **Should `laya_embedding_service.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11965811965811966 - nodes in this community are weakly interconnected._
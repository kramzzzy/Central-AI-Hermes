# Graph Report - Central-AI-Hermes  (2026-10-24)

## Corpus Check
- 152 files · ~131,711 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 13 file(s) not represented in the graph (top: (none) 5, .example 2, .patch 2)

## Summary
- 1988 nodes · 5345 edges · 90 communities (73 shown, 17 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 392 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9ef851d0`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- BackgroundTasks
- NativeChat
- production-bootstrap.mjs
- MemoryControls
- laya_embedding_service.py
- main.go
- run_settings
- pathlib
- FishCall
- re
- testing.T
- BackupError
- GoogleWorkspace
- hermes-os-mcp.py
- coolify_voice.py
- hermes_google.py
- answerIncomingWithVoice
- os-backup.py
- hermes_backups.py
- StreamTests
- main
- context.Context
- relayFanout
- hermes-job.py
- hermes_laya.py
- VoiceTests
- textBridge
- .test_resume_is_read_only_and_does_not_reload_history
- NativeSpeech
- local-bridge.py
- StreamPhoneCall
- hermes_settings.py
- Reader
- FreeSpeech
- whatsapp-call-voice.py
- callback.go
- SocialWorkspace
- AGENTS.md
- fakeRelay
- adapter.py
- .do_POST
- OSCalls
- whatsapp_system_context.py
- pairing.go
- worker.ts
- get_catalog_models
- TurnControl
- hermes_artwork.py
- json
- .connectFilter
- build_engine_adapter
- authenticated_exchange
- stream_conversation.py
- ConversationModelTests
- register
- Central AI — Hermes Intelligence & Superpowers Stack (Box 1)
- first-run-ui.js
- BridgeTests
- StreamingTurn
- laya-router-service.py
- os
- phone_cloud_asr.py
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
- .test_canceled_event_batch_cannot_emit_or_finish_a_reply
- TextModelProfileTests
- client.py
- fish_conversation.py
- PlaybackTests
- CallbackIntentTests
- IdleThread
- response
- calculate_solar_planning_v2
- Flagship Commercial Ads (`/ads`)
- Marketing (`/marketing`)
- Flagship Product Showcase (`/product`)
- Promotion (`/promotion`)
- SetupError
- native_call_speech.py
- hermes-memory-entry.py

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
- `_build_message_event()` --calls--> `attach_assistant_skills()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/whatsapp_system_context.py
- `_build_message_event()` --calls--> `get_whatsapp_reply_style()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/whatsapp_system_context.py
- `_poll_messages()` --indirect_call--> `route()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/laya_embedding_service.py

## Import Cycles
- None detected.

## Communities (90 total, 17 thin omitted)

### Community 0 - "BackgroundTasks"
Cohesion: 0.10
Nodes (5): TaskTests, factory(), CallbackTests, BackgroundTasks, session_ready()

### Community 1 - "NativeChat"
Cohesion: 0.06
Nodes (13): NativeChat, Installation, CallTaskChat, delivery_time(), next_time(), RoutineError, Routines, check_account() (+5 more)

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

### Community 7 - "pathlib"
Cohesion: 0.09
Nodes (5): SpeechPreferences, caller(), routing(), text_routes(), phone_speech()

### Community 8 - "FishCall"
Cohesion: 0.06
Nodes (10): fish_api(), PhoneAudioFilter, speech_model(), SpeechGate, start_audio_server(), handle(), FeedTests, GateTests (+2 more)

### Community 9 - "re"
Cohesion: 0.10
Nodes (13): installed(), profile_home(), identity_text(), rename_profile(), is_correction(), is_task_cancellation(), voice_reasoning_effort(), read_marker() (+5 more)

### Community 10 - "testing.T"
Cohesion: 0.12
Nodes (31): TestContinuousFilterWireAndReconnectKeepCallAlive(), TestFilterRequiresExactCallCredentials(), conversationFixture(), eventually(), TestBargeInDropsOldAudioAndKeepsOpeningWords(), TestContinuousReceiveCarriesAudioWhileReplyIsBusy(), TestFailureKeepsCallAliveAndNextTurnWorksWithoutReplay(), TestInterruptMergesOnlyUnsubmittedUtterances() (+23 more)

### Community 11 - "BackupError"
Cohesion: 0.19
Nodes (8): BackupError, Backups, redirect_request(), work(), serve(), do_GET(), do_POST(), reply()

### Community 12 - "GoogleWorkspace"
Cohesion: 0.18
Nodes (13): extract_email_content(), extract_parts(), GoogleError, GoogleWorkspace, fetch_single(), page(), identifier(), identity() (+5 more)

### Community 13 - "hermes-os-mcp.py"
Cohesion: 0.07
Nodes (24): call_app_os_api(), call_contact(), call_whatsapp_contact(), close_widget(), control_widget(), create_task(), delegate_specialists(), end_call() (+16 more)

### Community 14 - "coolify_voice.py"
Cohesion: 0.11
Nodes (29): apply(), config(), configured(), deployment_phase(), deployment_status(), fingerprint(), NoRedirect, pending() (+21 more)

### Community 16 - "answerIncomingWithVoice"
Cohesion: 0.11
Nodes (6): answerIncoming(), answerIncomingWithVoice(), TestIncomingAnswersBeforeSlowVoiceAndRetainsEarlyReady(), TestIncomingHangupCancelsPendingVoicePreparation(), fakeIncoming, incomingHandle

### Community 17 - "os-backup.py"
Cohesion: 0.10
Nodes (16): native_export(), NoRedirect, provider_request(), context(), command(), export(), filehash(), recovery() (+8 more)

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

### Community 23 - "hermes-job.py"
Cohesion: 0.23
Nodes (4): load_config(), field(), available_tools(), tool_options()

### Community 24 - "hermes_laya.py"
Cohesion: 0.27
Nodes (4): api_key(), available(), classify(), configured()

### Community 26 - "textBridge"
Cohesion: 0.13
Nodes (9): newTextBridge(), claimedCount(), TestTextAdmissionAndRouting(), TestTextReplyBindingAndSingleDelivery(), TestTextUncertainDeliveryCannotRetryAndClaimCannotReplay(), textFixture(), textRequest(), textBridge (+1 more)

### Community 27 - ".test_resume_is_read_only_and_does_not_reload_history"
Cohesion: 0.09
Nodes (3): NativeWarmupTests, owned_class(), TextBoundaryTests

### Community 29 - "local-bridge.py"
Cohesion: 0.19
Nodes (25): _append_customer_sync_queue(), _build_message_event(), _poll_messages(), send(), _calculate_solar_pre_assessment(), _fetch_live_box3_context(), _forward_customer_whatsapp_to_box3(), _is_premature_lead_handoff() (+17 more)

### Community 30 - "StreamPhoneCall"
Cohesion: 0.19
Nodes (4): PhonePCM, StreamPhoneCall, output(), output()

### Community 31 - "hermes_settings.py"
Cohesion: 0.15
Nodes (13): ExportError, progress(), read_bank(), at(), main(), idle(), put(), revision() (+5 more)

### Community 33 - "FreeSpeech"
Cohesion: 0.16
Nodes (3): fish_rest_tts(), FishRestSpeech, FreeSpeech

### Community 34 - "whatsapp-call-voice.py"
Cohesion: 0.08
Nodes (14): central_ai_identity(), get_business_knowledge_summary(), get_central_ai_identity(), get_company_name(), allowed_audio_call(), native_conversation(), private_phone_member(), task_native() (+6 more)

### Community 35 - "callback.go"
Cohesion: 0.14
Nodes (16): callbackControl(), callbackTargetAllowed(), claimTaskCallback(), claimTaskCallbackUsing(), finishTaskCallback(), TestCallbackClaimsAreBoundToThePolledOwner(), TestCallbackConfigurationCannotRedirectToAnotherNumber(), TestCallbackDoesNotPreparePrivateReportBeforeActualPeerAnswer() (+8 more)

### Community 36 - "SocialWorkspace"
Cohesion: 0.08
Nodes (13): clean_str(), SocialError, SocialWorkspace, save(), react(), send(), send(), validate_org() (+5 more)

### Community 37 - "AGENTS.md"
Cohesion: 0.18
Nodes (10): Assistant Identity & Voice Call Architecture, graphify Knowledge Graph & Architecture Navigation, Hindsight Persistent Agent Memory, Mandatory Agent Communication Policy: Minimal, Concise & Precise, Mandatory Box 1 Dual-Push Synchronization (`Central-AI-Hermes`), Mandatory Main Assistant Control for Every New Feature, Strict Global Design Pattern & UI/UX Policy, Strict Prohibition: No Remote / Live Server Access (Unless User Explicitly Allows) (+2 more)

### Community 38 - "fakeRelay"
Cohesion: 0.33
Nodes (4): newFakeRelay(), TestRelayFanoutMigratesReceivesAndUnblocksOnClose(), TestRelayFanoutRefreshesOnlyMatchingAllocateAndBroadcastsMedia(), fakeRelay

### Community 39 - "adapter.py"
Cohesion: 0.27
Nodes (7): _extract_and_analyze_customer_lead(), _format_natural_list(), _has_customer_confirmed_details(), _is_valid_confirmed_email(), _is_valid_confirmed_street_address(), _is_valid_person_name(), _parse_dict()

### Community 40 - ".do_POST"
Cohesion: 0.16
Nodes (8): export_authorized(), read_library(), complete(), validate(), profile_environment(), task_file_context(), Handler, start_routine_tick()

### Community 41 - "OSCalls"
Cohesion: 0.05
Nodes (16): OSCalls, dispatch(), reconcile(), configure_business_phone_runtime(), configure_phone_tools(), restrict_native_specialist_runtime(), NativeVoiceConversation, current_phone_chat() (+8 more)

### Community 42 - "whatsapp_system_context.py"
Cohesion: 0.10
Nodes (13): install_process_token(), attach_assistant_skills(), attach_booking_skill(), get_channel_context(), get_configured_assistant_name(), get_whatsapp_reply_style(), install_booking_skill(), _native_health() (+5 more)

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
Cohesion: 0.13
Nodes (13): main(), call_greeting(), transcribe(), native_enabled(), pcm(), OSStreamVoice, emit(), text() (+5 more)

### Community 47 - "hermes_artwork.py"
Cohesion: 0.12
Nodes (12): ArtworkError, available(), _download_image_to_b64(), _extract_b64_from_data_uri(), generate(), generate_marketing_image(), _generate_procedural_avatar(), generate_social_image() (+4 more)

### Community 48 - "json"
Cohesion: 0.27
Nodes (6): add_knowledge(), generate_image(), social_media(), add_knowledge(), generate_image(), social_media()

### Community 50 - "build_engine_adapter"
Cohesion: 0.13
Nodes (18): build_engine_adapter(), _complete(), connect(), _dispatch_inline_reply(), edit_message(), on_processing_complete(), _post_bridge_message(), _process_message_background() (+10 more)

### Community 51 - "authenticated_exchange"
Cohesion: 0.50
Nodes (3): authenticated_exchange(), google_from_chat(), inspect_from_chat()

### Community 53 - "stream_conversation.py"
Cohesion: 0.10
Nodes (10): conversation_llm(), is_casual_greeting_intent(), normalize_voice_greeting(), voice_call_note(), confirmed_text_key(), Conversation, _is_valid_openrouter_voice_model(), SpeculativeReply (+2 more)

### Community 55 - "register"
Cohesion: 0.50
Nodes (3): dashboard_origin(), register(), status()

### Community 56 - "Central AI — Hermes Intelligence & Superpowers Stack (Box 1)"
Cohesion: 0.22
Nodes (8): 🚀 1-Click Deployment on Coolify, 🏗️ Box 1 Architecture, Central AI — Hermes Intelligence & Superpowers Stack (Box 1), 🔒 Security & Decoupling Policy, 📦 Services Included, Step 1: Add Public Repository Resource, Step 2: Configure Environment Variables, Step 3: Click Deploy

### Community 57 - "first-run-ui.js"
Cohesion: 0.36
Nodes (6): api(), authDisplay(), install(), models, pollDeployment(), show()

### Community 58 - "BridgeTests"
Cohesion: 0.05
Nodes (7): NativeTaskBridge, OSNativeTaskBridge, StreamingRecognizer, StreamingTurn, BridgeTests, FishService, start()

### Community 61 - "os"
Cohesion: 0.06
Nodes (19): install_files(), profile_lock(), atomic_write(), profile_lock(), update_profile_instructions(), configure_identity(), atomic_write(), integration_config() (+11 more)

### Community 62 - "phone_cloud_asr.py"
Cohesion: 0.13
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
Cohesion: 0.33
Nodes (4): get_live_weather(), NoRedirect, read_weather(), WidgetWeatherTests

### Community 71 - "Handler"
Cohesion: 0.33
Nodes (3): connected(), finish(), Handler

### Community 72 - "Production Build Recipes & Deployment Architecture"
Cohesion: 0.40
Nodes (4): 3-Way Installation Sequence, Existing Box 1 installations: preserve data before the storage fix, Networking & Resolution, Production Build Recipes & Deployment Architecture

### Community 73 - "probe.go"
Cohesion: 0.20
Nodes (5): wireAudio(), probeAudio(), readProbeGreeting(), wireProbe(), outgoingRequest

### Community 78 - "client.py"
Cohesion: 0.19
Nodes (30): _app_os_urls(), _bridge_urls(), _call_app_os_mcp(), call_contact(), close_widget(), control_widget(), create_task(), _discovered_app_os_urls() (+22 more)

### Community 80 - "fish_conversation.py"
Cohesion: 0.19
Nodes (11): call_app_os_mcp(), call_whatsapp_action(), filter_playback_input(), normalized(), words(), is_goodbye_intent(), resolve_contact_target(), _resolve_hermes_api_key() (+3 more)

### Community 84 - "response"
Cohesion: 0.14
Nodes (6): openrouter_stream(), WorkspaceToolsTests, client(), open_request(), Response, response()

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
Nodes (4): Core Principles: Straightforward Copy + Flagship Product Ad Visuals, Optional Product Poster Structure, Promotion (`/promotion`), When to Use

### Community 92 - "SetupError"
Cohesion: 0.31
Nodes (3): NoRedirect, SetupError, WhatsAppSetup

### Community 95 - "native_call_speech.py"
Cohesion: 0.32
Nodes (3): _cached_piper_voice(), tts_config(), warm_piper()

### Community 100 - "hermes-memory-entry.py"
Cohesion: 0.50
Nodes (3): complete(), _build_responses_kwargs(), wire()

## Knowledge Gaps
- **17 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `BackgroundTasks` connect `BackgroundTasks` to `pathlib`, `FishCall`, `re`, `OSCalls`, `StreamTests`, `BridgeTests`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Should `BackgroundTasks` be split into smaller, more focused modules?**
  _Cohesion score 0.09567307692307692 - nodes in this community are weakly interconnected._
- **Why does `SocialWorkspace` connect `SocialWorkspace` to `json`, `os-backup.py`, `local-bridge.py`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Should `NativeChat` be split into smaller, more focused modules?**
  _Cohesion score 0.05898876404494382 - nodes in this community are weakly interconnected._
- **Why does `GoogleWorkspace` connect `GoogleWorkspace` to `local-bridge.py`, `hermes_google.py`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **Should `production-bootstrap.mjs` be split into smaller, more focused modules?**
  _Cohesion score 0.057297297297297295 - nodes in this community are weakly interconnected._
- **Should `laya_embedding_service.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11965811965811966 - nodes in this community are weakly interconnected._